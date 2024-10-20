import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from bs4 import BeautifulSoup, Tag
import requests
from urllib.parse import urljoin, urlparse
import re
import hashlib
import json
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import islice
import queue
import threading

class ScrapingProgress:
    """Class to manage and display scraping progress"""
    def __init__(self, progress_bar):
        self.progress_bar = progress_bar
        self.messages = []
        self.current_phase = None
        self.phase_progress = None
        self.stats = {
            'pages_discovered': 0,
            'links_found': 0,
            'dynamic_attempts': 0,
            'scroll_count': 0,
            'processed_urls': 0
        }
    
    def update_phase(self, phase, message):
        self.current_phase = phase
        self.log(f"Phase: {phase} - {message}")
        self._refresh_display()
    
    def update_stats(self, stat_name, value):
        self.stats[stat_name] = value
        self._refresh_display()
    
    def increment_stat(self, stat_name):
        self.stats[stat_name] += 1
        self._refresh_display()
    
    def log(self, message):
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.messages.append(f"{timestamp} - {message}")
        self._refresh_display()
    
    def _refresh_display(self):
        try:
            display_text = []
            
            if self.current_phase:
                display_text.append(f"**Current Phase: {self.current_phase}**\n")
            
            display_text.append("**Current Statistics:**")
            display_text.append(f"* Pages Discovered: {self.stats['pages_discovered']}")
            display_text.append(f"* Links Found: {self.stats['links_found']}")
            display_text.append(f"* Dynamic Load Attempts: {self.stats['dynamic_attempts']}")
            display_text.append(f"* Scroll Count: {self.stats['scroll_count']}")
            display_text.append(f"* Processed URLs: {self.stats['processed_urls']}")
            display_text.append("\n**Recent Activity:**")
            
            display_text.extend(self.messages[-10:])
            
            self.progress_bar.markdown('\n'.join(display_text))
        except Exception as e:
            pass

def create_chrome_options():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-popup-blocking')
    options.page_load_strategy = 'eager'
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    prefs = {
        'profile.managed_default_content_settings.images': 2,
        'disk-cache-size': 4096,
        'profile.managed_default_content_settings.javascript': 1
    }
    options.add_experimental_option('prefs', prefs)
    return options

def create_session():
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=100,
        pool_maxsize=100,
        max_retries=3,
        pool_block=False
    )
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    })
    return session

def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()

def clean_url(url):
    try:
        return url.split('#')[0] if '#' in url else url
    except:
        return url

class DriverPool:
    def __init__(self, size=3):
        self.drivers = queue.Queue()
        self.size = size
        for _ in range(size):
            self.drivers.put(webdriver.Chrome(service=Service(), options=create_chrome_options()))
            
    def get_driver(self):
        return self.drivers.get()
        
    def return_driver(self, driver):
        self.drivers.put(driver)
        
    def quit_all(self):
        while not self.drivers.empty():
            driver = self.drivers.get()
            try:
                driver.quit()
            except:
                pass


def should_exclude(element):
    exclude_classes = ['nav', 'menu', 'footer', 'sidebar', 'advertisement', 'cookie', 'popup', 'header']
    exclude_ids = ['nav', 'menu', 'footer', 'sidebar', 'ad', 'header']
    
    cookie_keywords = [
        'cookie', 'gdpr', 'privacy', 'tracking', 'analytics', 'consent',
        'session', 'storage', 'duration', 'browser', 'local storage',
        'pixel tracker', 'http cookie'
    ]
    
    try:
        text = element.get_text().lower()
        if any(keyword in text for keyword in cookie_keywords):
            return True

        for parent in element.parents:
            if parent.has_attr('class'):
                if any(cls in parent.get('class', []) for cls in exclude_classes):
                    return True
            if parent.has_attr('id'):
                if any(id in parent.get('id', '') for id in exclude_ids):
                    return True
    except:
        return False
    
    return False

def is_blog_post(text):
    blog_keywords = ['blog', 'post', 'article', 'news', 'story']
    return any(keyword in text.lower() for keyword in blog_keywords)

def is_after_date(text, target_date):
    if not target_date:
        return True
        
    date_patterns = [
        r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b',
        r'\b\d{4}-\d{2}-\d{2}\b',
        r'\b\d{2}/\d{2}/\d{4}\b'
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                date_str = match.group(0)
                if '-' in date_str:
                    date = datetime.strptime(date_str, '%Y-%m-%d')
                elif '/' in date_str:
                    date = datetime.strptime(date_str, '%m/%d/%Y')
                else:
                    date = datetime.strptime(date_str, '%b %d, %Y')
                return date >= target_date
            except:
                continue
    return True

def is_unwanted_link(url, base_url):
    unwanted_patterns = [
        '/cookie-policy', '/privacy-policy', '/terms-and-conditions',
        '/about-us', '/contact', '/careers', '/sitemap', '/login',
        '/register', '/signup', '/cart', '/checkout', '/account',
        '/wp-admin', '/wp-login', '/feed', '/rss', '/xmlrpc.php',
        'mailto:', 'tel:', 'javascript:'
    ]
    
    try:
        is_external = not url.startswith(base_url)
        is_unwanted = any(pattern in url.lower() for pattern in unwanted_patterns)
        return is_external or is_unwanted
    except:
        return True

def classify_url(url):
    try:
        url_lower = url.lower()
        
        if any(ext in url_lower for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
            return 'document'
            
        if any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp']):
            return 'image'
            
        if any(pattern in url_lower for pattern in [
            '/blog/', '/article/', '/post/', '/news/',
            '/resources/', '/insights/', '/knowledge/',
            '/case-study', '/white-paper', '/report'
        ]):
            return 'article'
            
        return 'page'
    except:
        return 'page'

def find_all_links(soup, base_url):
    """Universal link discovery with pagination support"""
    links = set()
    
    # Find all links regardless of structure
    all_links = soup.find_all('a', href=True)
    
    for link in all_links:
        try:
            href = link.get('href')
            if href and not href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                full_url = urljoin(base_url, href)
                if is_valid_url(full_url) and full_url.startswith(base_url):
                    links.add(clean_url(full_url))
        except Exception as e:
            print(f"Error processing link: {str(e)}")
            
    return links

def wait_for_dynamic_content(driver, attempts=3):
    """Enhanced dynamic content detection including pagination and infinite scroll"""
    
    # Common pagination selectors
    pagination_selectors = [
        '.pagination', 
        'nav[aria-label*="pagination"]',
        '[class*="pagination"]',
        '.load-more',
        '.infinite-scroll',
        '[data-page]',
        '.next',
        '[rel="next"]',
        'button[aria-label*="next"]',
        'a[aria-label*="next"]',
        '.archive__pagination',
        '.page-numbers'
    ]
    
    # Track initial content state
    initial_content = driver.page_source
    scroll_position = 0
    
    for _ in range(attempts):
        # 1. Check for clickable pagination
        for selector in pagination_selectors:
            try:
                pagination_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for element in pagination_elements:
                    if element.is_displayed() and element.is_enabled():
                        try:
                            element.click()
                            time.sleep(2)  # Wait for content load
                            return True
                        except:
                            continue
            except:
                continue
                
        # 2. Try infinite scroll
        last_height = driver.execute_script("return document.documentElement.scrollHeight")
        driver.execute_script(f"window.scrollTo(0, {last_height})")
        time.sleep(2)
        
        # 3. Check for new content
        new_content = driver.page_source
        new_height = driver.execute_script("return document.documentElement.scrollHeight")
        
        if new_content != initial_content or new_height > last_height:
            initial_content = new_content
            continue
        else:
            break
            
    return False

def handle_cookie_consent(driver):
    """Handle various cookie consent popup patterns"""
    consent_buttons = [
        'accept', 'agree', 'continue', 'got it', 'allow', 'consent',
        'accept all', 'allow all', 'accept cookies', 'okay',
        'i accept', 'i agree', 'accept necessary'
    ]
    
    for text in consent_buttons:
        try:
            buttons = driver.find_elements(By.XPATH, 
                f"//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]")
            for button in buttons:
                if button.is_displayed():
                    button.click()
                    time.sleep(1)
                    return True
        except:
            continue
    
    # Try common button IDs and classes
    consent_selectors = [
        '#accept-cookies', '.accept-cookies',
        '#cookie-accept', '.cookie-accept',
        '#cookie-consent-accept', '.cookie-consent-accept',
        '[aria-label*="accept cookies"]',
        '[data-cookieconsent="accept"]'
    ]
    
    for selector in consent_selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if element.is_displayed():
                    element.click()
                    time.sleep(1)
                    return True
        except:
            continue
            
    return False

def discover_site_content(driver_pool, base_url, progress, dynamic_limit=None):
    content_map = {
        'page': set(),
        'article': set(),
        'document': set(),
        'image': set(),
        'api_endpoints': set()
    }
    
    processed_urls = set()
    progress.update_phase("Initial Discovery", "Starting static content discovery")
    session = create_session()
    
    # Initial static discovery
    try:
        response = session.get(base_url)
        if response.ok:
            soup = BeautifulSoup(response.text, 'html.parser')
            initial_links = find_all_links(soup, base_url)
            progress.log(f"Found {len(initial_links)} initial links")
            progress.increment_stat('links_found')
            
            # Process found links
            for link in initial_links:
                if link.startswith(base_url):
                    content_type = classify_url(link)
                    clean_link = clean_url(link)
                    content_map[content_type].add(clean_link)
                    processed_urls.add(clean_link)
                    progress.increment_stat('pages_discovered')
    
    except Exception as e:
        progress.log(f"Error in initial discovery: {str(e)}")

    if dynamic_limit is not None:
        if dynamic_limit > 0:
            progress.update_phase("Dynamic Discovery", f"Starting dynamic content discovery (limit: {dynamic_limit} attempts)")
            try:
                driver = driver_pool.get_driver()
                try:
                    driver.get(base_url)
                    WebDriverWait(driver, 2).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                    handle_cookie_consent(driver)
                    
                    dynamic_attempts = 0
                    while dynamic_attempts < dynamic_limit:
                        if wait_for_dynamic_content(driver):
                            dynamic_attempts += 1
                            progress.update_stats('dynamic_attempts', dynamic_attempts)
                            
                            soup = BeautifulSoup(driver.page_source, 'html.parser')
                            new_links = find_all_links(soup, base_url)
                            progress.increment_stat('links_found')
                            
                            for link in new_links:
                                if link not in processed_urls and link.startswith(base_url):
                                    content_type = classify_url(link)
                                    content_map[content_type].add(link)
                                    processed_urls.add(link)
                                    progress.increment_stat('pages_discovered')
                        else:
                            break
                            
                finally:
                    driver_pool.return_driver(driver)
                    
            except Exception as e:
                progress.log(f"Notice: Dynamic discovery - {str(e)}")
        else:
            progress.log("Dynamic content discovery skipped (limit set to 0)")
    
    return content_map

def extract_content(driver_pool, url, content_type, base_url, exclude_types):
    """Optimized content extraction with enhanced dynamic content handling"""
    content = []
    driver = driver_pool.get_driver()
    clean_base_url = clean_url(url)
    
    try:
        driver.get(clean_base_url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        handle_cookie_consent(driver)
        wait_for_dynamic_content(driver)
        
        if content_type == 'document':
            content.append(f"[DOCUMENT] {clean_base_url}\n")
            return content
            
        if content_type == 'image':
            content.append(f"[IMAGE] {clean_base_url}\n")
            return content
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        seen_content = set()
        
        # Process schema.org data
        for element in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(element.string)
                if isinstance(data, dict):
                    for key in ['articleBody', 'description']:
                        if key in data:
                            text = clean_text(data[key])
                            content_hash = hashlib.md5(text.encode()).hexdigest()
                            if content_hash not in seen_content:
                                seen_content.add(content_hash)
                                content.append(f"[{key.upper()}] {text}\n")
            except:
                continue
        
        # Process main content
        main_selectors = [
            'article', 'main', '.content', '.post-content',
            '[role="main"]', '.entry-content', '.article-content',
            '.post-body', '.blog-post', '.article-body',
            '.card-body'
        ]
        
        for selector in main_selectors:
            try:
                main_content = soup.select_one(selector)
                if main_content:
                    for element in main_content.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'a']):
                        if should_exclude(element):
                            continue
                            
                        if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li']:
                            if 'text' not in exclude_types:
                                text = clean_text(element.get_text())
                                if text and len(text) > 20:
                                    content_hash = hashlib.md5(text.encode()).hexdigest()
                                    if content_hash not in seen_content:
                                        seen_content.add(content_hash)
                                        content.append(f"[{element.name.upper()}] {text}\n")
                        elif element.name == 'a' and 'links' not in exclude_types:
                            href = element.get('href')
                            if href:
                                full_url = urljoin(base_url, href)
                                if href.startswith(('http', 'https')):
                                    content.append(f"[EXTERNAL_LINK] {full_url}\n")
                                elif href.startswith('/'):
                                    content.append(f"[INTERNAL_LINK] {full_url}\n")
                    if content:
                        break
            except:
                continue
                
    except Exception as e:
        content.append(f"[ERROR] Failed to extract content: {str(e)}\n")
    finally:
        driver_pool.return_driver(driver)
        
    return content

def scrape_pages(base_url, initial_url, max_depth, exclude_types, max_urls, target_date, progress_container, dynamic_limit=None):
    all_content = []
    driver_pool = DriverPool(size=3)
    progress = ScrapingProgress(progress_container)
    
    try:
        content_map = discover_site_content(driver_pool, base_url, progress, dynamic_limit)
        
        if not any(content_map.values()):
            progress.log("No content discovered. Please check the URL.")
            return []
        
        urls_to_process = []
        for content_type, urls in content_map.items():
            for url in urls:
                clean_base_url = clean_url(url)
                if clean_base_url not in {u[0] for u in urls_to_process}:
                    urls_to_process.append((clean_base_url, content_type))
        
        if max_urls:
            urls_to_process = urls_to_process[:max_urls]
        
        total = len(urls_to_process)
        progress.update_phase("Content Extraction", f"Processing {total} items")
        
        processed_count = 0
        batch_size = 5
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            for i in range(0, len(urls_to_process), batch_size):
                batch = urls_to_process[i:i + batch_size]
                futures = []
                
                for url, content_type in batch:
                    futures.append(
                        executor.submit(
                            extract_content,
                            driver_pool,
                            url,
                            content_type,
                            base_url,
                            exclude_types
                        )
                    )
                
                for future in as_completed(futures):
                    try:
                        content = future.result()
                        processed_count += 1
                        progress.update_stats('processed_urls', processed_count)
                        
                        if content:
                            url = urls_to_process[processed_count - 1][0]
                            all_content.extend([f"\n[URL] {url}\n"])
                            all_content.extend(content)
                            if url not in st.session_state.scraped_urls:
                                st.session_state.scraped_urls.append(url)
                    except Exception as e:
                        progress.log(f"Notice: {str(e)}")
                        continue
                
                progress.log(f"Processed {min(i + batch_size, total)}/{total} items")
                
    finally:
        driver_pool.quit_all()
    
    progress.update_phase("Complete", "Scraping finished")
    return all_content

def main():
    st.title("Advanced Web Scraper for Competitor Analysis")

    if 'messages' in st.session_state:
        del st.session_state.messages

    col1, col2 = st.columns([2, 1])
    
    with col1:
        url = st.text_input("Enter the website URL to scrape:")
        max_depth = st.number_input("Enter the maximum depth to scrape:", min_value=0, max_value=5, value=1, step=1)
        max_urls = st.number_input("Maximum number of URLs to scrape (leave blank for no limit):", min_value=1, value=None)
    
    with col2:
        dynamic_limit = st.number_input(
            "Dynamic content discovery limit (0 to disable, blank for unlimited):", 
            min_value=0, 
            value=None
        )
        date_filter = st.date_input("Only include content published after:", value=None)
    
    exclude_types = st.multiselect(
        "Select content types to exclude:",
        ['text', 'links', 'images', 'blog posts'],
        default=[]
    )
    
    if st.button("Scrape"):
        if not is_valid_url(url):
            st.error("Please enter a valid URL.")
            return
        
        st.info("Scraping in progress...")
        progress_container = st.empty()
        st.session_state.scraped_urls = []
        
        try:
            target_date = datetime.combine(date_filter, datetime.min.time()) if date_filter else None
            content = scrape_pages(url, url, max_depth, exclude_types, max_urls, target_date, progress_container, dynamic_limit)
            
            if content:
                filename = f"{urlparse(url).netloc}_analysis.txt"
                with open(filename, "w", encoding="utf-8") as f:
                    for line in content:
                        if 'blog posts' in exclude_types and is_blog_post(line):
                            continue
                        f.write(line)
                
                st.success(f"Analysis completed! Content saved to {filename}")
                
                with open(filename, "r", encoding="utf-8") as f:
                    file_content = f.read()
                
                st.download_button(
                    label="Download Content",
                    data=file_content,
                    file_name=filename,
                    mime="text/plain"
                )
                
                st.subheader("Preview of Extracted Content")
                st.text_area("Content Preview", value="".join(content[:20]), height=300)
                
                st.subheader("Scraped URLs")
                for url in st.session_state.scraped_urls:
                    st.write(url)
            else:
                st.warning("No content could be extracted. Please check the URL and try again.")
            
        except Exception as e:
            st.error(f"Error during scraping: {str(e)}")

if __name__ == "__main__":
    main()
