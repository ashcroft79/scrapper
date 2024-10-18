import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
from collections import defaultdict
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pickle
import os

class UniversalWebScraper:
    def __init__(self, base_url, max_depth=3, exclusions=None, include_params=False):
        self.base_url = base_url
        self.max_depth = max_depth
        self.exclusions = exclusions or []
        self.include_params = include_params
        self.site_map = defaultdict(list)
        self.visited = set()

    def is_valid_url(self, url):
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except ValueError:
            return False

    def normalize_url(self, url):
        if not self.include_params:
            url = url.split('?')[0]
        return url.rstrip('/')

    def should_exclude(self, url):
        return any(exclusion in url for exclusion in self.exclusions)

    def get_links(self, url):
        try:
            response = requests.get(url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            return [urljoin(url, link.get('href')) for link in soup.find_all('a', href=True)]
        except Exception as e:
            st.error(f"Error fetching links from {url}: {str(e)}")
            return []

    def create_site_map(self):
        to_visit = [(self.base_url, 0)]
        with ThreadPoolExecutor(max_workers=5) as executor:
            while to_visit:
                url, depth = to_visit.pop(0)
                if depth > self.max_depth:
                    continue
            normalized_url = self.normalize_url(url)
            if normalized_url not in self.visited and self.is_valid_url(normalized_url) and not self.should_exclude(normalized_url):
                self.visited.add(normalized_url)
                self.site_map[depth].append(normalized_url)
                links = self.get_links(normalized_url)
                to_visit.extend((link, depth + 1) for link in links if link.startswith(self.base_url))
            time.sleep(0.1)  # Be polite to the server

    def extract_content(self, url):
        try:
            response = requests.get(url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove unwanted elements
            for unwanted in soup(['script', 'style', 'nav', 'footer']):
                unwanted.decompose()
            
            # Extract text content
            text_content = soup.get_text(separator='\n', strip=True)
            
            # Clean up the text
            text_content = re.sub(r'\n+', '\n', text_content)
            text_content = re.sub(r'\s+', ' ', text_content)
            
            return text_content.strip()
        except Exception as e:
            st.error(f"Error extracting content from {url}: {str(e)}")
            return ""

    def scrape_selected_urls(self, selected_urls):
        results = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_url = {executor.submit(self.extract_content, url): url for url in selected_urls}
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    content = future.result()
                    results[url] = content
                except Exception as e:
                    st.error(f"Error scraping {url}: {str(e)}")
        return results

@st.cache_data
def create_site_map(base_url, max_depth, exclusions, include_params):
    scraper = UniversalWebScraper(base_url, max_depth, exclusions, include_params)
    scraper.create_site_map()
    return scraper.site_map

def main():
    st.title("Universal Web Scraper")

    # Use session state to store the site map
    if 'site_map' not in st.session_state:
        st.session_state.site_map = None

    base_url = st.text_input("Enter the base URL to scrape:")
    max_depth = st.number_input("Maximum depth to crawl:", min_value=1, max_value=5, value=3)
    include_params = st.checkbox("Include URL parameters in site map")
    
    exclusions = st.text_area("Enter URL patterns to exclude (one per line):").split('\n')
    exclusions = [e.strip() for e in exclusions if e.strip()]
    
    if st.button("Create Site Map"):
        if not base_url:
            st.error("Please enter a base URL.")
            return

        with st.spinner("Creating site map..."):
            st.session_state.site_map = create_site_map(base_url, max_depth, exclusions, include_params)

        st.success("Site map created!")

    if st.session_state.site_map:
        st.subheader("Site Map")
        for depth, urls in st.session_state.site_map.items():
            with st.expander(f"Depth {depth} ({len(urls)} URLs)"):
                selected_urls = st.multiselect(f"Select URLs to scrape at depth {depth}:", urls, key=f"depth_{depth}")
                if selected_urls:
                    if st.button(f"Scrape selected URLs at depth {depth}", key=f"scrape_{depth}"):
                        scraper = UniversalWebScraper(base_url, max_depth, exclusions, include_params)
                        with st.spinner("Scraping selected URLs..."):
                            results = scraper.scrape_selected_urls(selected_urls)
                        
                        for url, content in results.items():
                            st.subheader(f"Content from {url}")
                            st.text_area("", content, height=200, key=f"content_{url}")
                            
                            # Save content to a file
                            filename = f"{urlparse(url).netloc}_{urlparse(url).path.replace('/', '_')}.txt"
                            with open(filename, "w", encoding="utf-8") as f:
                                f.write(content)
                            st.download_button(
                                label=f"Download content for {url}",
                                data=content,
                                file_name=filename,
                                mime="text/plain",
                                key=f"download_{url}"
                            )

if __name__ == "__main__":
    main()