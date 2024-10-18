import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import hashlib
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

def create_chrome_options():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    return options

def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

def clean_text(text):
    return re.sub(r'\s+', ' ', text).strip()

def should_exclude(element):
    exclude_classes = ['nav', 'menu', 'footer', 'sidebar', 'advertisement', 'cookie', 'popup']
    exclude_ids = ['nav', 'menu', 'footer', 'sidebar', 'ad']
    
    cookie_keywords = [
        'cookie', 'gdpr', 'privacy', 'tracking', 'analytics', 'consent',
        'session', 'storage', 'duration', 'browser', 'local storage',
        'pixel tracker', 'http cookie'
    ]
    
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
    
    return False

def is_blog_post(text):
    blog_keywords = ['blog', 'post', 'article', 'news']
    return any(keyword in text.lower() for keyword in blog_keywords)

def is_after_date(text, target_date):
    date_pattern = r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b'
    match = re.search(date_pattern, text)
    if match:
        date_str = match.group(0)
        date = datetime.strptime(date_str, '%b %d, %Y')
        return date >= target_date
    return True

def is_unwanted_link(url, base_url):
    unwanted_patterns = [
        '/cookie-policy', '/privacy-policy', '/terms-and-conditions',
        '/about-us', '/contact', '/careers', '/sitemap'
    ]
    is_external = not url.startswith(base_url)
    return any(pattern in url.lower() for pattern in unwanted_patterns) or is_external

def handle_cookie_consent(driver):
    try:
        accept_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"))
        )
        accept_button.click()
        time.sleep(1)
    except:
        pass

def gather_page_content(driver, base_url):
    """Gather content from current page"""
    links = []
    
    # Get all article links on current page
    article_links = [link.get_attribute('href') for link in driver.find_elements(By.TAG_NAME, 'a')
                    if link.get_attribute('href') and 
                    is_valid_url(link.get_attribute('href')) and 
                    link.get_attribute('href').startswith(base_url) and
                    '/hub/' in link.get_attribute('href')]
    
    links.extend(article_links)
    return list(set(links))  # Remove duplicates

def load_more_content(driver, base_url):
    """Load and gather content from all pages"""
    all_links = []
    
    try:
        while True:
            # Gather content from current page
            current_links = gather_page_content(driver, base_url)
            all_links.extend(current_links)
            
            try:
                # Find next button
                next_button = None
                page_buttons = driver.find_elements(By.CSS_SELECTOR, "button.archive__pagination__number")
                current_page = next(
                    (btn for btn in page_buttons if 'active' in btn.get_attribute('class').split()), 
                    None
                )
                
                if current_page:
                    current_num = int(current_page.text)
                    next_button = next(
                        (btn for btn in page_buttons if int(btn.text) == current_num + 1),
                        None
                    )
                
                if not next_button:
                    next_button = driver.find_element(
                        By.CSS_SELECTOR, 
                        "button.archive__pagination__arrow--next:not([disabled])"
                    )
                
                if not next_button:
                    break
                    
                # Click and wait for content
                driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", next_button)
                time.sleep(3)
                
            except (NoSuchElementException, StaleElementReferenceException):
                break
                
    except Exception as e:
        st.error(f"Error loading more content: {str(e)}")
        
    return list(set(all_links))  # Remove duplicates

def extract_content(driver, base_url, exclude_types):
    content = []
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    for element in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'a']):
        if should_exclude(element):
            continue

        if element.name in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li']:
            if 'text' not in exclude_types:
                text = clean_text(element.get_text())
                if text and len(text) > 20:
                    content.append(f"[{element.name.upper()}] {text}\n")
        elif element.name == 'a' and 'links' not in exclude_types:
            href = element.get('href')
            if href:
                if href.startswith(('http', 'https')):
                    content.append(f"[EXTERNAL LINK] {href}\n")
                elif href.startswith('/'):
                    content.append(f"[INTERNAL LINK] {urljoin(base_url, href)}\n")

    return content

def scrape_single_page(url, base_url, exclude_types):
    options = create_chrome_options()
    driver = webdriver.Chrome(service=Service(), options=options)
    try:
        driver.get(url)
        handle_cookie_consent(driver)
        time.sleep(2)
        return extract_content(driver, base_url, exclude_types)
    finally:
        driver.quit()

def scrape_pages(base_url, initial_url, max_depth, exclude_types, max_urls, target_date, progress_bar):
    visited = set()
    all_content = []
    
    # First get all paginated links
    options = create_chrome_options()
    driver = webdriver.Chrome(service=Service(), options=options)
    try:
        driver.get(initial_url)
        handle_cookie_consent(driver)
        time.sleep(2)
        all_links = load_more_content(driver, base_url)
    finally:
        driver.quit()
    
    # Now scrape each article in parallel
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_url = {}
        for url in all_links:
            if max_urls is None or len(visited) < max_urls:
                if url not in visited and not is_unwanted_link(url, base_url):
                    visited.add(url)
                    future_to_url[executor.submit(scrape_single_page, url, base_url, exclude_types)] = url

        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                content = future.result()
                progress_bar.text(f"Scraped: {url}")
                st.session_state.scraped_urls.append(url)
                all_content.extend([f"\n[URL] {url}\n"])
                all_content.extend(content)
            except Exception as e:
                st.error(f"Error scraping {url}: {str(e)}")

    return all_content

def main():
    st.title("Advanced Web Scraper for Competitor Analysis")

    url = st.text_input("Enter the website URL to scrape:")
    max_depth = st.number_input("Enter the maximum depth to scrape:", min_value=0, max_value=5, value=1, step=1)
    max_urls = st.number_input("Maximum number of URLs to scrape (leave blank for no limit):", min_value=1, value=None)
    date_filter = st.date_input("Only include content published after (leave blank for no filter):", value=None)
    
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
        progress_bar = st.empty()
        st.session_state.scraped_urls = []
        
        try:
            target_date = datetime.combine(date_filter, datetime.min.time()) if date_filter else None
            content = scrape_pages(url, url, max_depth, exclude_types, max_urls, target_date, progress_bar)
            
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