#!/usr/bin/env python3
"""
NetCrawl - Ultimate Website Crawler & Directory Discovery Tool
Author: TnYtCoder
Version: 2.0
"""

import requests
import re
import os
import sys
import json
import time
import random
import signal
import hashlib
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup
from datetime import datetime
from collections import deque, defaultdict
import concurrent.futures
import threading
import warnings
from typing import Set, Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum

# ===== COLORAMA SETUP =====
try:
    from colorama import init, Fore, Back, Style
    init(autoreset=True)
    COLORS = True
    GREEN = Fore.GREEN
    RED = Fore.RED
    YELLOW = Fore.YELLOW
    BLUE = Fore.BLUE
    MAGENTA = Fore.MAGENTA
    CYAN = Fore.CYAN
    WHITE = Fore.WHITE
    BRIGHT = Style.BRIGHT
    DIM = Style.DIM
    RESET = Style.RESET_ALL
except ImportError:
    COLORS = False
    # Fallback if colorama not installed
    GREEN = ''
    RED = ''
    YELLOW = ''
    BLUE = ''
    MAGENTA = ''
    CYAN = ''
    WHITE = ''
    BRIGHT = ''
    DIM = ''
    RESET = ''

warnings.filterwarnings('ignore')

# ===== ASCII BANNER (YOURS - PRESERVED) =====
BANNER = f"""
{BRIGHT}{CYAN}     / _ \\
   \\_\\(_)/_/  {GREEN}NetCrawl {WHITE}- TnYtCoder {RESET}{CYAN}
    _//"\\\\_     {YELLOW}- Website Crawler & Directory Discovery {RESET}{CYAN}
     /   \\

{RESET}"""


# ===== ENUMS & DATA CLASSES =====
class FileType(Enum):
    HTML = 'html'
    JS = 'js'
    CSS = 'css'
    IMAGES = 'images'
    DOCUMENTS = 'documents'
    API = 'api'
    OTHER = 'other'


@dataclass
class CrawlStats:
    """Statistics for crawling session"""
    start_time: float = 0.0
    end_time: float = 0.0
    total_urls: int = 0
    total_directories: int = 0
    total_files: int = 0
    errors: int = 0
    requests_made: int = 0
    bytes_downloaded: int = 0
    
    @property
    def crawl_time(self) -> float:
        return self.end_time - self.start_time if self.end_time else 0


class RateLimiter:
    """Intelligent rate limiter to avoid getting blocked"""
    
    def __init__(self, max_requests: int = 15, per_seconds: int = 1, 
                 jitter: bool = True, respect_robots: bool = True):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self.jitter = jitter
        self.respect_robots = respect_robots
        self.requests = deque()
        self.robots_delay = 0
        self.lock = threading.Lock()
        
    def set_robots_delay(self, delay: float):
        """Set delay from robots.txt"""
        self.robots_delay = delay
        
    def wait(self):
        """Wait if necessary based on rate limits"""
        with self.lock:
            now = time.time()
            
            # Clean old requests
            while self.requests and self.requests[0] < now - self.per_seconds:
                self.requests.popleft()
            
            # Calculate required delay
            base_delay = 0
            if len(self.requests) >= self.max_requests:
                base_delay = self.requests[0] + self.per_seconds - now
            
            # Add robots.txt delay
            total_delay = max(base_delay, self.robots_delay)
            
            # Add jitter to appear more human
            if self.jitter and total_delay > 0:
                total_delay += random.uniform(0.1, 0.5)
            
            if total_delay > 0:
                time.sleep(total_delay)
            
            self.requests.append(time.time())


class URLFilter:
    """Smart URL filtering to avoid duplicates and noise"""
    
    def __init__(self, domain: str):
        self.domain = domain
        self.seen_urls: Set[str] = set()
        self.url_hashes: Set[str] = set()
        
        # Patterns to block
        self.blocked_patterns = [
            r'javascript:', r'mailto:', r'tel:', r'data:',
            r'about:', r'blob:', r'ftp:', r'file:',
            r'facebook\.com/tr', r'google-analytics',
            r'doubleclick\.net', r'googletagmanager',
            r'addthis\.com', r'discourse\.org',
            r'wp-json', r'wp-includes', r'wp-content/plugins',
        ]
        
        # File extensions to skip
        self.skip_extensions = {
            '.jpg', '.jpeg', '.png', '.gif', '.svg', '.ico', '.webp', '.bmp',
            '.woff', '.woff2', '.ttf', '.eot', '.otf',
            '.mp4', '.mp3', '.avi', '.mov', '.wmv', '.flv', '.mkv',
            '.zip', '.tar', '.gz', '.rar', '.7z', '.bz2',
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
            '.exe', '.msi', '.bin', '.dmg', '.iso',
            '.map', '.txt', '.xml', '.json', '.csv'
        }
        
    def normalize(self, url: str) -> str:
        """Normalize URL for comparison"""
        url = url.rstrip('/')
        url = unquote(url)
        
        # Remove fragments
        if '#' in url:
            url = url.split('#')[0]
        
        # Remove common tracking parameters
        parsed = urlparse(url)
        query = parsed.query
        if query:
            # Remove tracking params
            params = query.split('&')
            filtered = []
            for param in params:
                if not any(p in param.lower() for p in ['utm_', 'fbclid', 'gclid', 'ref=']):
                    filtered.append(param)
            
            if filtered:
                new_query = '&'.join(filtered)
                url = url.replace(query, new_query)
            else:
                url = url.replace('?' + query, '')
        
        return url
    
    def is_valid(self, url: str) -> Tuple[bool, str]:
        """Check if URL is valid for crawling"""
        try:
            parsed = urlparse(url)
            
            # Must have scheme and netloc
            if not parsed.scheme or not parsed.netloc:
                return False, "Missing scheme or netloc"
            
            # Must be same domain
            if parsed.netloc != self.domain:
                return False, "Different domain"
            
            # Check blocked patterns
            for pattern in self.blocked_patterns:
                if re.search(pattern, url, re.IGNORECASE):
                    return False, f"Blocked pattern: {pattern}"
            
            # Check extensions
            path = parsed.path.lower()
            ext = os.path.splitext(path)[1]
            if ext in self.skip_extensions:
                return False, f"Skipped extension: {ext}"
            
            # Check for duplicates using hash for long URLs
            normalized = self.normalize(url)
            if normalized in self.seen_urls:
                return False, "Duplicate URL"
            
            # For very long URLs, use hash
            if len(normalized) > 500:
                url_hash = hashlib.md5(normalized.encode()).hexdigest()
                if url_hash in self.url_hashes:
                    return False, "Duplicate hash"
                self.url_hashes.add(url_hash)
            else:
                self.seen_urls.add(normalized)
            
            return True, "Valid"
            
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def add_url(self, url: str):
        """Manually add URL to seen set"""
        normalized = self.normalize(url)
        if len(normalized) > 500:
            self.url_hashes.add(hashlib.md5(normalized.encode()).hexdigest())
        else:
            self.seen_urls.add(normalized)


# ===== MAIN CRAWLER CLASS =====
class NetCrawl:
    def __init__(self, target_url: str, max_depth: int = 3, max_threads: int = 10,
                 max_urls: int = 10000, timeout: int = 15, delay: float = 0.5):
        self.target_url = target_url.rstrip('/')
        self.parsed_url = urlparse(target_url)
        self.domain = self.parsed_url.netloc
        self.max_depth = max_depth
        self.max_threads = max_threads
        self.max_urls = max_urls
        self.timeout = timeout
        self.delay = delay
        
        # Advanced session with retries
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': self._get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'DNT': '1'
        })
        self.session.verify = False
        
        # Retry adapter
        adapter = requests.adapters.HTTPAdapter(
            max_retries=3,
            pool_connections=100,
            pool_maxsize=100
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        
        # Storage
        self.url_filter = URLFilter(self.domain)
        self.url_queue: deque = deque()
        self.visited_urls: Set[str] = set()
        self.discovered_urls: Set[str] = set()
        self.directories: Set[str] = set()
        self.files: Dict[str, Set[str]] = {
            'html': set(),
            'js': set(),
            'css': set(),
            'images': set(),
            'documents': set(),
            'api': set(),
            'other': set()
        }
        
        # Rate limiting
        self.rate_limiter = RateLimiter(max_requests=15, per_seconds=1)
        
        # Statistics
        self.stats = CrawlStats()
        self.lock = threading.Lock()
        self.stop_flag = False
        
        # Signal handling
        signal.signal(signal.SIGINT, self._signal_handler)
        
        self._print_banner()
    
    def _get_random_user_agent(self) -> str:
        """Get random user agent to avoid detection"""
        agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7; rv:109.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (X11; Linux i686; rv:109.0) Gecko/20100101 Firefox/121.0',
        ]
        return random.choice(agents)
    
    def _print_banner(self):
        """Print the ASCII banner (preserved)"""
        print(BANNER)
    
    def _signal_handler(self, sig, frame):
        """Handle Ctrl+C gracefully"""
        print(f"\n\n{BRIGHT}{RED}[!] Interrupt received, stopping gracefully...{RESET}")
        self.stop_flag = True
    
    def _log(self, message: str, level: str = 'info', end: str = '\n'):
        """Colored logging"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        colors = {
            'info': f"{BLUE}[*]{RESET}",
            'success': f"{GREEN}[+]{RESET}",
            'error': f"{RED}[!]{RESET}",
            'warning': f"{YELLOW}[?]{RESET}",
            'debug': f"{MAGENTA}[D]{RESET}",
            'found': f"{GREEN}[✓]{RESET}",
            'crawl': f"{CYAN}[→]{RESET}"
        }
        
        prefix = colors.get(level, f"{WHITE}[ ]{RESET}")
        print(f"{prefix} {BRIGHT}{WHITE}{timestamp}{RESET} - {message}", end=end)
    
    def _update_stats(self, **kwargs):
        """Thread-safe stats update"""
        with self.lock:
            for key, value in kwargs.items():
                if hasattr(self.stats, key):
                    setattr(self.stats, getattr(self.stats, key) + value)
    
    def _categorize_url(self, url: str) -> FileType:
        """Categorize URL by file type"""
        path = urlparse(url).path.lower()
        
        # Extract extension
        ext = os.path.splitext(path)[1] if '.' in os.path.basename(path) else ''
        
        # API endpoints
        if any(p in path for p in ['/api/', '/rest/', '/graphql', '/v1/', '/v2/']):
            return FileType.API
        
        # HTML pages
        if ext in ['', '.html', '.htm', '.php', '.asp', '.aspx', '.jsp', '.do']:
            return FileType.HTML
        
        # JavaScript
        if ext in ['.js', '.jsx', '.ts', '.tsx', '.mjs']:
            return FileType.JS
        
        # CSS
        if ext in ['.css', '.scss', '.sass', '.less', '.styl']:
            return FileType.CSS
        
        # Images
        if ext in ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.ico', '.webp', '.bmp']:
            return FileType.IMAGES
        
        # Documents
        if ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv']:
            return FileType.DOCUMENTS
        
        return FileType.OTHER
    
    def _extract_directory(self, url: str) -> str:
        """Extract directory path from URL"""
        parsed = urlparse(url)
        path = parsed.path
        
        if not path or path == '/':
            return '/'
        
        # If path ends with /, it's a directory
        if path.endswith('/'):
            return path
        
        # Otherwise, get parent directory
        directory = os.path.dirname(path)
        if not directory:
            directory = '/'
        
        return directory if directory.endswith('/') else directory + '/'
    
    def _extract_links(self, url: str, html: str) -> Set[str]:
        """Extract all links from HTML content"""
        links = set()
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # All tags with href/src
            extractors = [
                ('a', 'href'),
                ('link', 'href'),
                ('script', 'src'),
                ('img', 'src'),
                ('iframe', 'src'),
                ('frame', 'src'),
                ('form', 'action'),
                ('area', 'href'),
                ('base', 'href')
            ]
            
            for tag, attr in extractors:
                for element in soup.find_all(tag, **{attr: True}):
                    link = element[attr].strip()
                    if link:
                        full_url = urljoin(url, link)
                        links.add(full_url)
            
            # Extract from inline JavaScript
            js_patterns = [
                r'["\'](https?://[^"\']+)["\']',
                r'["\'](/[^"\']+)["\']',
                r"['\"](/[^'\"]+)['\"]",
                r'url\(["\']?([^"\')]+)["\']?\)'
            ]
            
            for pattern in js_patterns:
                matches = re.findall(pattern, html)
                for match in matches:
                    if match and not match.startswith(('data:', 'javascript:')):
                        full_url = urljoin(url, match)
                        links.add(full_url)
            
        except Exception as e:
            self._log(f"Error extracting links: {e}", 'debug')
        
        return links
    
    def _fetch_url(self, url: str) -> Optional[requests.Response]:
        """Fetch URL with rate limiting and error handling"""
        self.rate_limiter.wait()
        
        try:
            with self.lock:
                self.stats.requests_made += 1
            
            response = self.session.get(
                url, 
                timeout=self.timeout,
                allow_redirects=True,
                stream=True
            )
            
            # Track bandwidth
            content_length = len(response.content)
            with self.lock:
                self.stats.bytes_downloaded += content_length
            
            return response
            
        except requests.exceptions.Timeout:
            self._log(f"Timeout: {url}", 'debug')
        except requests.exceptions.ConnectionError:
            self._log(f"Connection error: {url}", 'debug')
        except requests.exceptions.TooManyRedirects:
            self._log(f"Too many redirects: {url}", 'debug')
        except Exception as e:
            self._log(f"Error fetching {url}: {str(e)}", 'debug')
        
        return None
    
    def _process_url(self, url: str, depth: int):
        """Process a single URL"""
        if self.stop_flag:
            return
        
        # Validate URL
        is_valid, reason = self.url_filter.is_valid(url)
        if not is_valid:
            return
        
        self.url_filter.add_url(url)
        
        with self.lock:
            self.visited_urls.add(url)
            self.discovered_urls.add(url)
        
        self._log(f"Crawling: {url} (Depth: {depth})", 'crawl')
        
        # Fetch URL
        response = self._fetch_url(url)
        if not response:
            with self.lock:
                self.stats.errors += 1
            return
        
        # Categorize
        file_type = self._categorize_url(url)
        with self.lock:
            self.files[file_type.value].add(url)
        
        # Extract directory
        directory = self._extract_directory(url)
        with self.lock:
            self.directories.add(directory)
        
        # Only parse HTML for links
        content_type = response.headers.get('Content-Type', '')
        if 'text/html' not in content_type and 'application/xhtml' not in content_type:
            return
        
        if depth >= self.max_depth:
            return
        
        # Extract links
        links = self._extract_links(url, response.text)
        
        # Add new URLs to queue
        with self.lock:
            for link in links:
                if link not in self.visited_urls and len(self.discovered_urls) < self.max_urls:
                    self.url_queue.append((link, depth + 1))
    
    def _crawl_worker(self):
        """Worker thread for crawling"""
        while not self.stop_flag and self.url_queue:
            try:
                url, depth = self.url_queue.popleft()
                self._process_url(url, depth)
            except IndexError:
                break
            except Exception as e:
                self._log(f"Worker error: {e}", 'error')
    
    def _check_robots_txt(self):
        """Check robots.txt for rules and sitemaps"""
        self._log("Checking robots.txt...", 'info')
        
        robots_url = urljoin(self.target_url, '/robots.txt')
        response = self._fetch_url(robots_url)
        
        if response and response.status_code == 200:
            with self.lock:
                self.files['other'].add(robots_url)
                self.discovered_urls.add(robots_url)
            
            self._log("Found robots.txt", 'success')
            
            # Parse robots.txt
            lines = response.text.split('\n')
            sitemaps = []
            
            for line in lines:
                line = line.strip()
                
                # Check for crawl delay
                if line.lower().startswith('crawl-delay'):
                    try:
                        delay = float(line.split(':')[1].strip())
                        self.rate_limiter.set_robots_delay(delay)
                        self._log(f"Crawl-Delay: {delay}s", 'info')
                    except:
                        pass
                
                # Check for sitemaps
                if line.lower().startswith('sitemap:'):
                    sitemap_url = line.split(':', 1)[1].strip()
                    sitemaps.append(sitemap_url)
                
                # Check for disallowed paths
                if line.lower().startswith('disallow:'):
                    path = line.split(':', 1)[1].strip()
                    if path and path != '/':
                        full_url = urljoin(self.target_url, path)
                        is_valid, _ = self.url_filter.is_valid(full_url)
                        if is_valid:
                            self._log(f"Disallowed path: {path} (found in robots.txt)", 'debug')
            
            # Parse sitemaps
            for sitemap_url in sitemaps:
                self._parse_sitemap(sitemap_url)
    
    def _parse_sitemap(self, sitemap_url: str):
        """Parse sitemap for URLs"""
        self._log(f"Parsing sitemap: {sitemap_url}", 'info')
        
        response = self._fetch_url(sitemap_url)
        if not response:
            return
        
        with self.lock:
            self.files['other'].add(sitemap_url)
            self.discovered_urls.add(sitemap_url)
        
        # Extract URLs from sitemap
        url_pattern = r'<loc>(.*?)</loc>'
        urls = re.findall(url_pattern, response.text, re.IGNORECASE)
        
        for url in urls:
            url = url.strip()
            is_valid, _ = self.url_filter.is_valid(url)
            if is_valid:
                with self.lock:
                    self.url_queue.append((url, 0))
                    self.discovered_urls.add(url)
        
        self._log(f"Found {len(urls)} URLs in sitemap", 'success')
    
    def _discover_common_paths(self):
        """Discover common paths and directories"""
        self._log("Discovering common paths...", 'info')
        
        common_paths = [
            # Admin panels
            '/admin', '/administrator', '/login', '/wp-admin', '/dashboard',
            '/panel', '/console', '/cpanel', '/manager', '/backend',
            
            # API endpoints
            '/api', '/api/v1', '/api/v2', '/api/v3', '/rest', '/graphql',
            '/swagger', '/swagger-ui', '/api-docs', '/docs', '/redoc',
            
            # Common directories
            '/assets', '/static', '/public', '/uploads', '/upload', '/files',
            '/downloads', '/images', '/img', '/css', '/js', '/fonts',
            '/backup', '/backups', '/old', '/test', '/dev', '/staging',
            
            # Config files
            '/.env', '/.git/config', '/.gitignore', '/.htaccess',
            '/web.config', '/config.php', '/config.json', '/config.yml',
            '/package.json', '/composer.json', '/requirements.txt',
            '/docker-compose.yml', '/Dockerfile', '/.dockerignore',
            
            # Security files
            '/robots.txt', '/sitemap.xml', '/sitemap_index.xml',
            '/.well-known/security.txt', '/humans.txt', '/security.txt',
            '/crossdomain.xml', '/clientaccesspolicy.xml',
            
            # Development files
            '/phpinfo.php', '/info.php', '/test.php', '/debug.php',
            '/error_log', '/debug.log', '/access.log', '/error.log',
            
            # Database files
            '/database.sql', '/db.sql', '/backup.sql', '/dump.sql',
            '/data.sql', '/schema.sql', '/mysql.sql',
            
            # Source control
            '/.git/', '/.svn/', '/.hg/', '/.bzr/',
            '/.git/HEAD', '/.git/config', '/.git/index',
            
            # WordPress specific
            '/wp-content/', '/wp-includes/', '/wp-json/',
            '/wp-content/uploads/', '/wp-content/plugins/',
            '/wp-content/themes/', '/wp-content/cache/',
            
            # Common login pages
            '/user/login', '/users/login', '/admin/login', '/login.php',
            '/signin', '/sign-in', '/logon', '/log-in', '/auth',
            
            # Common file extensions
            '/backup.zip', '/backup.tar', '/backup.tar.gz',
            '/www.zip', '/website.zip', '/site.zip',
            '/.bak', '/.old', '/.orig', '/.backup'
        ]
        
        found_count = 0
        
        def check_path(path: str):
            url = urljoin(self.target_url, path)
            
            try:
                response = self.session.head(
                    url, 
                    timeout=3, 
                    allow_redirects=False,
                    headers={'User-Agent': self._get_random_user_agent()}
                )
                
                # Check if accessible
                if response.status_code in [200, 201, 202, 203, 204, 301, 302, 307, 308, 401, 403]:
                    is_valid, _ = self.url_filter.is_valid(url)
                    if is_valid:
                        self.url_filter.add_url(url)
                        
                        with self.lock:
                            self.discovered_urls.add(url)
                            file_type = self._categorize_url(url)
                            self.files[file_type.value].add(url)
                            
                            directory = self._extract_directory(url)
                            self.directories.add(directory)
                        
                        status_color = GREEN if response.status_code < 300 else YELLOW
                        status_text = f"{status_color}{response.status_code}{RESET}"
                        self._log(f"Found: {url} ({status_text})", 'found')
                        
                        return True
            except:
                pass
            
            return False
        
        # Check paths with thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            results = list(executor.map(check_path, common_paths))
            found_count = sum(results)
        
        self._log(f"Discovered {found_count} common paths", 'success')
    
    def start_crawl(self):
        """Start the crawling process"""
        self._log(f"{BRIGHT}Starting crawl of {GREEN}{self.target_url}{RESET}", 'info')
        self._log(f"Max depth: {self.max_depth}, Threads: {self.max_threads}", 'info')
        self._log(f"Max URLs: {self.max_urls}, Timeout: {self.timeout}s", 'info')
        self._log("=" * 60, 'debug')
        
        self.stats.start_time = time.time()
        
        try:
            # Check robots.txt first
            self._check_robots_txt()
            
            # Add initial URL
            self.url_queue.append((self.target_url, 0))
            
            # Create thread pool
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_threads) as executor:
                futures = []
                for _ in range(min(self.max_threads, len(self.url_queue) or 1)):
                    future = executor.submit(self._crawl_worker)
                    futures.append(future)
                
                # Monitor progress
                while not self.stop_flag and any(not f.done() for f in futures):
                    time.sleep(0.5)
                    
                    # Show progress
                    with self.lock:
                        progress = f"{CYAN}Progress: {GREEN}{len(self.visited_urls)}/{len(self.discovered_urls)} URLs{RESET}"
                        if self.max_urls:
                            progress += f" {YELLOW}({len(self.discovered_urls)}/{self.max_urls}){RESET}"
                        print(f"\r{progress}    ", end='')
            
            # Discover common paths
            if not self.stop_flag:
                self._discover_common_paths()
            
        except Exception as e:
            self._log(f"Critical error: {e}", 'error')
        
        self.stats.end_time = time.time()
        
        # Update final stats
        self.stats.total_urls = len(self.discovered_urls)
        self.stats.total_directories = len(self.directories)
        self.stats.total_files = sum(len(files) for files in self.files.values())
    
    def generate_report(self):
        """Generate comprehensive report"""
        print(f"\n\n{BRIGHT}{'='*70}{RESET}")
        print(f"{BRIGHT}{GREEN}📊 CRAWL REPORT{RESET}")
        print(f"{BRIGHT}{'='*70}{RESET}")
        
        # Basic info
        print(f"\n{BRIGHT}🎯 Target:{RESET} {CYAN}{self.target_url}{RESET}")
        print(f"{BRIGHT}🕐 Time:{RESET} {self.stats.crawl_time:.2f} seconds")
        print(f"{BRIGHT}📊 Requests:{RESET} {self.stats.requests_made}")
        print(f"{BRIGHT}📦 Data:{RESET} {self.stats.bytes_downloaded / 1024:.2f} KB")
        print(f"{BRIGHT}❌ Errors:{RESET} {RED if self.stats.errors else GREEN}{self.stats.errors}{RESET}")
        
        # Summary
        print(f"\n{BRIGHT}{GREEN}📈 SUMMARY{RESET}")
        print(f"{BRIGHT}{'-'*40}{RESET}")
        print(f"{BRIGHT}Total URLs:{RESET} {GREEN}{self.stats.total_urls}{RESET}")
        print(f"{BRIGHT}Total Directories:{RESET} {GREEN}{self.stats.total_directories}{RESET}")
        print(f"{BRIGHT}Total Files:{RESET} {GREEN}{self.stats.total_files}{RESET}")
        
        # Files by type
        print(f"\n{BRIGHT}{BLUE}📁 FILES BY TYPE{RESET}")
        print(f"{BRIGHT}{'-'*40}{RESET}")
        
        type_colors = {
            'html': GREEN,
            'js': YELLOW,
            'css': BLUE,
            'images': MAGENTA,
            'documents': CYAN,
            'api': RED,
            'other': WHITE
        }
        
        for file_type, urls in self.files.items():
            if urls:
                color = type_colors.get(file_type, WHITE)
                print(f"{color}▸ {file_type.upper()}:{RESET} {len(urls)} files")
        
        # Top directories
        if self.directories:
            print(f"\n{BRIGHT}{YELLOW}📂 TOP DIRECTORIES{RESET}")
            print(f"{BRIGHT}{'-'*40}{RESET}")
            
            # Count URLs per directory
            dir_counts = defaultdict(int)
            for url in self.discovered_urls:
                dir_path = self._extract_directory(url)
                dir_counts[dir_path] += 1
            
            # Sort by count
            top_dirs = sorted(dir_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            
            for dir_path, count in top_dirs:
                bar_length = int((count / max(dir_counts.values())) * 20)
                bar = f"{GREEN}{'█' * bar_length}{WHITE}{'░' * (20 - bar_length)}{RESET}"
                print(f"  {CYAN}{dir_path[:30]:30}{RESET} {bar} {count}")
        
        # Sample URLs
        if self.discovered_urls:
            print(f"\n{BRIGHT}{MAGENTA}🔍 SAMPLE URLs{RESET}")
            print(f"{BRIGHT}{'-'*40}{RESET}")
            
            # Show different types
            shown = set()
            categories_shown = 0
            
            for file_type in FileType:
                type_urls = [u for u in self.discovered_urls if self._categorize_url(u) == file_type]
                if type_urls and categories_shown < 5:
                    url = random.choice(list(type_urls))
                    color = type_colors.get(file_type.value, WHITE)
                    print(f"  {color}▸ {file_type.value.upper()}:{RESET} {url[:60]}...")
                    categories_shown += 1
    
    def save_results(self):
        """Save results in multiple formats"""
        print(f"\n{BRIGHT}{'='*70}{RESET}")
        
        # Ask user
        response = input(f"{YELLOW}Save results? (txt/json/both/no): {RESET}").lower().strip()
        
        if response in ['no', 'n']:
            print(f"{RED}Results not saved{RESET}")
            return
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_filename = f"netcrawl_{self.domain}_{timestamp}"
        
        # Save TXT
        if response in ['txt', 'both']:
            self._save_txt(f"{base_filename}.txt")
        
        # Save JSON
        if response in ['json', 'both']:
            self._save_json(f"{base_filename}.json")
        
        print(f"{GREEN}✅ Results saved successfully!{RESET}")
    
    def _save_txt(self, filename: str):
        """Save as text file"""
        content = []
        
        content.append("="*80)
        content.append("NETCRAWL - Website Crawl Report")
        content.append(f"Author: TnYtCoder")
        content.append("="*80)
        content.append("")
        content.append(f"Target: {self.target_url}")
        content.append(f"Domain: {self.domain}")
        content.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        content.append(f"Crawl Time: {self.stats.crawl_time:.2f} seconds")
        content.append(f"Requests Made: {self.stats.requests_made}")
        content.append(f"Data Downloaded: {self.stats.bytes_downloaded / 1024:.2f} KB")
        content.append("")
        
        content.append("="*80)
        content.append("SUMMARY")
        content.append("="*80)
        content.append(f"Total URLs: {self.stats.total_urls}")
        content.append(f"Total Directories: {self.stats.total_directories}")
        content.append(f"Total Files: {self.stats.total_files}")
        content.append("")
        content.append("Files by Type:")
        for file_type, urls in self.files.items():
            if urls:
                content.append(f"  {file_type.upper()}: {len(urls)}")
        content.append("")
        
        content.append("="*80)
        content.append("ALL DISCOVERED URLS")
        content.append("="*80)
        content.append("")
        
        for url in sorted(self.discovered_urls):
            content.append(url)
        
        content.append("")
        content.append("="*80)
        content.append("DIRECTORIES")
        content.append("="*80)
        content.append("")
        
        for directory in sorted(self.directories):
            content.append(directory)
        
        content.append("")
        content.append("="*80)
        content.append("FILES BY CATEGORY")
        content.append("="*80)
        content.append("")
        
        for file_type, urls in self.files.items():
            if urls:
                content.append(f"\n{file_type.upper()}:")
                content.append("-" * 40)
                for url in sorted(urls):
                    content.append(f"  {url}")
        
        content.append("")
        content.append("="*80)
        content.append(f"End of Report - Generated by NetCrawl v2.0 (TnYtCoder)")
        content.append("="*80)
        
        # Write to file
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(content))
            
            print(f"{GREEN}[+] Saved TXT: {filename} ({len(content)} lines){RESET}")
        
        except Exception as e:
            print(f"{RED}[!] Error saving TXT: {e}{RESET}")
    
    def _save_json(self, filename: str):
        """Save as JSON file"""
        data = {
            'tool': 'NetCrawl',
            'author': 'TnYtCoder',
            'version': '2.0',
            'target': {
                'url': self.target_url,
                'domain': self.domain
            },
            'stats': {
                'crawl_time': self.stats.crawl_time,
                'requests_made': self.stats.requests_made,
                'bytes_downloaded': self.stats.bytes_downloaded,
                'errors': self.stats.errors,
                'total_urls': self.stats.total_urls,
                'total_directories': self.stats.total_directories,
                'total_files': self.stats.total_files
            },
            'directories': sorted(self.directories),
            'files': {
                k: sorted(v) for k, v in self.files.items()
            },
            'all_urls': sorted(self.discovered_urls),
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            
            file_size = os.path.getsize(filename)
            print(f"{GREEN}[+] Saved JSON: {filename} ({file_size} bytes){RESET}")
        
        except Exception as e:
            print(f"{RED}[!] Error saving JSON: {e}{RESET}")


# ===== MAIN ENTRY POINT =====
def main():
    """Main entry point"""
    # Declare global variables FIRST
    global COLORS, GREEN, RED, YELLOW, BLUE, MAGENTA, CYAN, WHITE, BRIGHT, DIM, RESET
    
    if len(sys.argv) < 2:
        print(BANNER)
        print(f"{BRIGHT}{CYAN}Usage:{RESET} python netcrawl.py {GREEN}<target_url>{RESET} [options]\n")
        print(f"{BRIGHT}{YELLOW}Options:{RESET}")
        print(f"  {GREEN}--depth <n>{RESET}     Maximum crawl depth (default: 3)")
        print(f"  {GREEN}--threads <n>{RESET}   Number of threads (default: 10)")
        print(f"  {GREEN}--max-urls <n>{RESET}  Maximum URLs to crawl (default: 10000)")
        print(f"  {GREEN}--timeout <n>{RESET}   Request timeout in seconds (default: 15)")
        print(f"  {GREEN}--delay <n>{RESET}     Delay between requests (default: 0.5)")
        print(f"  {GREEN}--no-color{RESET}      Disable colored output")
        print(f"  {GREEN}--help{RESET}          Show this help message\n")
        print(f"{BRIGHT}{CYAN}Examples:{RESET}")
        print(f"  python netcrawl.py https://example.com")
        print(f"  python netcrawl.py https://example.com --depth 5 --threads 20")
        print(f"  python netcrawl.py https://example.com --max-urls 5000\n")
        sys.exit(0)
    
    # Parse arguments
    target_url = sys.argv[1]
    
    # Default values
    max_depth = 3
    max_threads = 10
    max_urls = 10000
    timeout = 15
    delay = 0.5
    
    # Parse options
    for i, arg in enumerate(sys.argv):
        if arg == '--depth' and i + 1 < len(sys.argv):
            max_depth = int(sys.argv[i + 1])
        elif arg == '--threads' and i + 1 < len(sys.argv):
            max_threads = int(sys.argv[i + 1])
        elif arg == '--max-urls' and i + 1 < len(sys.argv):
            max_urls = int(sys.argv[i + 1])
        elif arg == '--timeout' and i + 1 < len(sys.argv):
            timeout = int(sys.argv[i + 1])
        elif arg == '--delay' and i + 1 < len(sys.argv):
            delay = float(sys.argv[i + 1])
        elif arg == '--no-color':
            COLORS = False
            GREEN = RED = YELLOW = BLUE = MAGENTA = CYAN = WHITE = BRIGHT = DIM = RESET = ''
    
    # Legal warning
    print(f"\n{BRIGHT}{RED}{'='*60}{RESET}")
    print(f"{BRIGHT}{RED}⚠️  LEGAL WARNING{RESET}")
    print(f"{BRIGHT}{RED}{'='*60}{RESET}")
    print(f"{YELLOW}[!] Only crawl websites you own or have permission to test{RESET}")
    print(f"{YELLOW}[!] Unauthorized crawling may violate terms of service{RESET}")
    print(f"{YELLOW}[!] Use responsibly and ethically{RESET}")
    print(f"{BRIGHT}{RED}{'='*60}{RESET}\n")
    
    response = input(f"{BRIGHT}Do you have authorization to crawl this target? (yes/no): {RESET}")
    if response.lower() not in ['yes', 'y']:
        print(f"{RED}[!] Exiting - Authorization required{RESET}")
        sys.exit(1)
    
    # Create crawler
    crawler = NetCrawl(
        target_url=target_url,
        max_depth=max_depth,
        max_threads=max_threads,
        max_urls=max_urls,
        timeout=timeout,
        delay=delay
    )
    
    # Start crawling
    crawler.start_crawl()
    
    # Generate report
    crawler.generate_report()
    
    # Save results
    crawler.save_results()
    
    print(f"\n{BRIGHT}{GREEN}✅ Crawl complete!{RESET}")
    print(f"{BRIGHT}{YELLOW}[!] Remember: Use responsibly and ethically!{RESET}\n")


if __name__ == "__main__":
    main()
