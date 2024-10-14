import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import os

def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

def scrape_page(url, depth, max_depth, visited, base_url):
    if depth > max_depth or url in visited:
        return ""

    visited.add(url)
    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract text content
        text_content = soup.get_text(separator='\n', strip=True)
        
        # Extract image URLs
        image_urls = [urljoin(base_url, img['src']) for img in soup.find_all('img') if 'src' in img.attrs]
        
        # Extract video links
        video_links = [urljoin(base_url, video['src']) for video in soup.find_all('video') if 'src' in video.attrs]
        
        # Recursively scrape linked pages
        for link in soup.find_all('a', href=True):
            next_url = urljoin(base_url, link['href'])
            if is_valid_url(next_url) and urlparse(next_url).netloc == urlparse(base_url).netloc:
                text_content += scrape_page(next_url, depth + 1, max_depth, visited, base_url)
        
        return f"URL: {url}\n\nText Content:\n{text_content}\n\nImage URLs:\n{', '.join(image_urls)}\n\nVideo Links:\n{', '.join(video_links)}\n\n{'='*50}\n\n"
    
    except Exception as e:
        return f"Error scraping {url}: {str(e)}\n\n"

def main():
    st.title("Website Scraper")

    url = st.text_input("Enter the website URL to scrape:")
    max_depth = st.number_input("Enter the maximum depth to scrape:", min_value=1, value=1, step=1)
    
    if st.button("Scrape"):
        if not is_valid_url(url):
            st.error("Please enter a valid URL.")
            return
        
        st.info("Scraping in progress...")
        content = scrape_page(url, 0, max_depth, set(), url)
        
        # Save content to a file
        filename = "scraped_content.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        
        st.success(f"Scraping completed! Content saved to {filename}")
        st.download_button(label="Download Scraped Content", data=content, file_name=filename, mime="text/plain")

if __name__ == "__main__":
    main()
