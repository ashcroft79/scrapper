import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from bs4 import BeautifulSoup
import requests
from urllib.parse import urljoin, urlparse
import re
import hashlib
import json
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue
import threading

class ScrapingProgress:
    def __init__(self, progress_bar):
        self.progress_bar = progress_bar
        self.messages = []
        self.current_phase = None
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
            for stat, value in self.stats.items():
                display_text.append(f"* {stat.replace('_', ' ').title()}: {value}")
            
            display_text.append("\n**Recent Activity:**")
            display_text.extend(self.messages[-10:])
            
            self.progress_bar.markdown('\n'.join(display_text))
        except Exception as e:
            pass

class WebDriver:
    def __init__(self):
        self.options = Options()
        self._configure_options()
        
    def _configure_options(self):
        self.options.add_argument('--headless')
        self.options.add_argument('--no-sandbox')
        self.options.add_argument('--disable-dev-shm-usage')
        self.options.add_argument('--window-size=1920,1080')
        self.options.page_load_strategy = 'eager'
        self.options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
        
    def create_driver(self):
        return webdriver.Chrome(service=Service(), options=self.options)

class DriverPool:
    def __init__(self, size=3):
        self.web_driver = WebDriver()
        self.drivers = queue.Queue()
        self.size = size
        for _ in range(size):
            self.drivers.put(self.web_driver.create_driver())
    
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

class ContentExtractor:
    def __init__(self, driver_pool, progress):
        self.driver_pool = driver_pool
        self.progress = progress
        self.session = self._create_session()

    def _create_session(self):
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=100,
            pool_maxsize=100,
            max_retries=3
        )
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5'
        })
        return session

    def discover_site_content(self, base_url, dynamic_limit=None):
        content_map = {
            'page': set(),
            'article': set(),
            'document': set(),
            'image': set(),
            'api_endpoints': set()
        }
        
        processed_urls = set()
        self.progress.update_phase("Initial Discovery", "Starting content discovery")
        
        try:
            # Initial static discovery
            response = self.session.get(base_url)
            if response.ok:
                soup = BeautifulSoup(response.text, 'html.parser')
                initial_links = self._find_all_links(soup, base_url)
                self.progress.log(f"Found {len(initial_links)} initial links")
                
                for link in initial_links:
                    if link.startswith(base_url):
                        self._process_link(link, content_map, processed_urls)

            # Dynamic discovery
            if dynamic_limit:
                self._handle_dynamic_discovery(base_url, content_map, processed_urls, dynamic_limit)
                
        except Exception as e:
            self.progress.log(f"Error in discovery: {str(e)}")
            
        return content_map, processed_urls

    def _handle_dynamic_discovery(self, base_url, content_map, processed_urls, dynamic_limit):
        self.progress.update_phase("Dynamic Discovery", f"Starting dynamic discovery (limit: {dynamic_limit})")
        driver = self.driver_pool.get_driver()
        
        try:
            driver.get(base_url)
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            self._handle_cookie_consent(driver)
            
            dynamic_attempts = 0
            while dynamic_attempts < dynamic_limit:
                if self._wait_for_dynamic_content(driver):
                    dynamic_attempts += 1
                    self.progress.update_stats('dynamic_attempts', dynamic_attempts)
                    
                    soup = BeautifulSoup(driver.page_source, 'html.parser')
                    new_links = self._find_all_links(soup, base_url)
                    
                    for link in new_links:
                        if link not in processed_urls and link.startswith(base_url):
                            self._process_link(link, content_map, processed_urls)
                else:
                    break
                    
        finally:
            self.driver_pool.return_driver(driver)

    def _wait_for_dynamic_content(self, driver, timeout=30):
        pagination_selectors = [
            '.pagination', 'nav[aria-label*="pagination"]', '[class*="pagination"]',
            '.load-more', '.infinite-scroll', '[data-page]', '.next', '[rel="next"]',
            'button[aria-label*="next"]', 'a[aria-label*="next"]'
        ]
        
        start_time = time.time()
        last_height = driver.execute_script("return document.documentElement.scrollHeight")
        initial_content = driver.page_source
        
        while time.time() - start_time < timeout:
            # Try pagination elements
            for selector in pagination_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            element.click()
                            time.sleep(2)
                            if driver.page_source != initial_content:
                                return True
                except:
                    continue
            
            # Try infinite scroll
            driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
            time.sleep(2)
            
            new_height = driver.execute_script("return document.documentElement.scrollHeight")
            if new_height != last_height:
                last_height = new_height
                initial_content = driver.page_source
                return True
                
        return False

    def _handle_cookie_consent(self, driver):
        consent_buttons = [
            'accept', 'agree', 'continue', 'got it', 'allow', 'consent',
            'accept all', 'allow all', 'accept cookies'
        ]
        
        for text in consent_buttons:
            try:
                buttons = driver.find_elements(By.XPATH, 
                    f"//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text}')]")
                for button in buttons:
                    if button.is_displayed():
                        button.click()
                        time.sleep(1)
                        return
            except:
                continue

    def _find_all_links(self, soup, base_url):
        links = set()
        for link in soup.find_all('a', href=True):
            try:
                href = link.get('href')
                if href and not href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                    full_url = urljoin(base_url, href)
                    if self._is_valid_url(full_url) and full_url.startswith(base_url):
                        links.add(self._clean_url(full_url))
            except Exception as e:
                self.progress.log(f"Error processing link: {str(e)}")
        return links

    def _process_link(self, link, content_map, processed_urls):
        content_type = self._classify_url(link)
        clean_link = self._clean_url(link)
        content_map[content_type].add(clean_link)
        processed_urls.add(clean_link)
        self.progress.increment_stat('pages_discovered')

    @staticmethod
    def _is_valid_url(url):
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except:
            return False

    @staticmethod
    def _clean_url(url):
        return url.split('#')[0] if '#' in url else url

    @staticmethod
    def _classify_url(url):
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

def main():
    st.title("Advanced Web Scraper for Competitor Analysis")

    col1, col2 = st.columns([2, 1])
    
    with col1:
        url = st.text_input("Enter the website URL to scrape:")
        max_depth = st.number_input("Enter the maximum depth to scrape:", min_value=0, max_value=5, value=1, step=1)
        max_urls = st.number_input("Maximum number of URLs to scrape (leave blank for no limit):", min_value=1, value=None)
    
    with col2:
        dynamic_limit = st.number_input(
            "Dynamic content discovery limit (0 to disable, blank for unlimited):", 
            min_value=0, 
            value=2
        )
        date_filter = st.date_input("Only include content published after:", value=None)
    
    exclude_types = st.multiselect(
        "Select content types to exclude:",
        ['text', 'links', 'images', 'blog posts'],
        default=[]
    )
    
    if st.button("Scrape"):
        if not ContentExtractor._is_valid_url(url):
            st.error("Please enter a valid URL.")
            return
            
        st.info("Scraping in progress...")
        progress_container = st.empty()
        progress = ScrapingProgress(progress_container)
        
        try:
            driver_pool = DriverPool(size=3)
            extractor = ContentExtractor(driver_pool, progress)
            content_map, processed_urls = extractor.discover_site_content(url, dynamic_limit)
            
            if not any(content_map.values()):
                st.warning("No content could be extracted. Please check the URL and try again.")
                return
                
            # Process and save results
            filename = f"{urlparse(url).netloc}_analysis.txt"
            with open(filename, "w", encoding="utf-8") as f:
                for content_type, urls in content_map.items():
                    f.write(f"\n=== {content_type.upper()} ===\n")
                    for url in urls:
                        f.write(f"{url}\n")
            
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
            st.text_area("Content Preview", value=file_content[:1000], height=300)
            
        except Exception as e:
            st.error(f"Error during scraping: {str(e)}")
        finally:
            if 'driver_pool' in locals():
                driver_pool.quit_all()

if __name__ == "__main__":
    main()
