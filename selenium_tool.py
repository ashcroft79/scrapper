import streamlit as st
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import hashlib
import time
from datetime import datetime

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

def handle_cookie_consent(driver):
    try:
        accept_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"))
        )
        accept_button.click()
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "main.content"))
        )
    except:
        pass

def is_unwanted_link(url, base_url):
    unwanted_patterns = [
        '/cookie-policy', '/privacy-policy', '/terms-and-conditions',
        '/about-us', '/contact', '/careers', '/sitemap'
    ]
    is_external = not url.startswith(base_url)
    return any(pattern in url.lower() for pattern in unwanted_patterns) or is_external

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

def load_more_content(driver):
    try:
        while True:
            load_more = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".load-more-button"))
            )
            driver.execute_script("arguments[0].click();", load_more)
            time.sleep(2)
    except:
        pass

def scrape_page(driver, base_url, url, depth, max_depth, visited, exclude_types, max_urls, target_date, progress_bar):
    if depth > max_depth or url in visited or (max_urls is not None and len(visited) >= max_urls):
        return []

    if not url.startswith(base_url) or is_unwanted_link(url, base_url):
        return []

    visited.add(url)
    content = []

    try:
        driver.get(url)
        handle_cookie_consent(driver)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        progress_bar.text(f"Scraping: {url}")
        st.session_state.scraped_urls.append(url)

        load_more_content(driver)

        page_content = extract_content(driver, base_url, exclude_types)
        if target_date is None or is_after_date("\n".join(page_content), target_date):
            content.extend([f"\n[URL] {url}\n"])
            content.extend(page_content)

        if depth < max_depth and (max_urls is None or len(visited) < max_urls):
            links = driver.find_elements(By.TAG_NAME, 'a')
            internal_links = [link.get_attribute('href') for link in links 
                              if is_valid_url(link.get_attribute('href')) 
                              and link.get_attribute('href').startswith(base_url)
                              and link.get_attribute('href') != url
                              and not is_unwanted_link(link.get_attribute('href'), base_url)]
            
            for next_url in set(internal_links):
                content.extend(scrape_page(driver, base_url, next_url, depth + 1, max_depth, visited, exclude_types, max_urls, target_date, progress_bar))

    except Exception as e:
        st.error(f"Error scraping {url}: {str(e)}")

    return content

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
        
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        driver = webdriver.Chrome(options=chrome_options)
        
        try:
            target_date = datetime.combine(date_filter, datetime.min.time()) if date_filter else None
            content = scrape_page(driver, url, url, 0, max_depth, set(), exclude_types, max_urls, target_date, progress_bar)
            
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
                st.write(st.session_state.scraped_urls)
            else:
                st.warning("No content could be extracted. Please check the URL and try again.")
        
        finally:
            driver.quit()

if __name__ == "__main__":
    main()