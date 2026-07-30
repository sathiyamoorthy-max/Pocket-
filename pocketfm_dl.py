import asyncio
import requests
import os
import re
import subprocess
import logging
import tempfile
import m3u8
from playwright.async_api import async_playwright
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

logger = logging.getLogger(__name__)

async def async_download(url):
    try:
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
        
        cookie_file = 'cookies.txt'
        
        async with async_playwright() as p:
            # Chromium browser ஐ Headless முறையில் ரன் செய்தல்
            browser = await p.chromium.launch(
                headless=True, 
                args=['--no-sandbox', '--disable-dev-shm-usage']
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
            )
            
            # Cookies-ஐ Playwright-க்குள் உட்புகுத்துதல்
            if os.path.exists(cookie_file):
                with open(cookie_file, 'r') as f:
                    for line in f:
                        if not line.strip() or line.startswith('#'):
                            continue
                        parts = line.strip().split('\t')
                        if len(parts) >= 7:
                            await context.add_cookies([{
                                'name': parts[5],
                                'value': parts[6],
                                'domain': parts[0],
                                'path': parts[2]
                            }])
                logger.info("Cookies successfully injected into browser.")
            
            page = await context.new_page()
            logger.info(f"Navigating to {url}...")
            
            # Network-ல் M3U8 கோரிக்கையை காத்திருக்கும்
            try:
                # ரிக்வெஸ்ட் லிங்கில் ".m3u8" இருக்கும் முதல் response-ஐ பிடிக்கும்
                response = await page.wait_for_response(
                    lambda response: '.m3u8' in response.url,
                    timeout=30000
                )
                m3u8_url = response.url
                logger.info(f"Found M3U8 Playlist URL: {m3u8_url}")
            except Exception:
                # வெப் பக்கத்தின் HTML-ல் இருந்து Regex மூலம் M3U8-ஐ தேடுதல்
                page_content = await page.content()
                m3u8_match = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', page_content)
                if m3u8_match:
                    m3u8_url = m3u8_match.group(1)
                    logger.info(f"Found M3U8 URL via HTML regex: {m3u8_url}")
                else:
                    raise Exception("M3U8 URL not found in network or HTML.")
            
            # Playwright ஐ மூடுதல்
            await browser.close()
            
            # M3U8-ஐ செயலாக்கி ஆடியோவை டவுன்லோட் செய்யும் துணை செயல்பாடு
            return await process_m3u8(m3u8_url)

    except Exception as e:
        logger.error(f"Error in advanced OTT downloader: {e}")
        raise Exception(f"Download failed: {e}")

async def process_m3u8(m3u8_url):
    try:
        # M3U8 பிளேலிஸ்ட்டை டவுன்லோட் செய்தல்
        playlist = m3u8.load(m3u8_url)
        base_url = '/'.join(m3u8_url.split('/')[:-1])
        
        # AES Key-ஐ கண்டுபிடித்தல்
        key_uri = None
        iv = None
        if playlist.keys:
            key_obj = playlist.keys[0]
            if key_obj and key_obj.uri:
                key_uri = key_obj.uri
                if key_obj.iv:
                    iv = key_obj.iv
                logger.info(f"Found AES Encryption Key URI: {key_uri}")

        # Key பைலை டவுன்லோட் செய்தல்
        aes_key = None
        if key_uri:
            if not key_uri.startswith('http'):
                key_url = f"{base_url}/{key_uri}"
            else:
                key_url = key_uri
            response = requests.get(key_url)
            aes_key = response.content
            logger.info("AES Key downloaded successfully.")

        # ஆடியோ துண்டுகளை (Segments) டவுன்லோட் செய்தல்
        segment_urls = [seg.uri for seg in playlist.segments]
        if not segment_urls:
            raise Exception("No audio segments found in M3U8.")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            ts_files = []
            
            for i, seg_url in enumerate(segment_urls):
                if not seg_url.startswith('http'):
                    seg_url = f"{base_url}/{seg_url}"
                
                ts_path = os.path.join(tmpdir, f"chunk_{i:04d}.ts")
                logger.info(f"Downloading segment {i+1}/{len(segment_urls)}...")
                
                response = requests.get(seg_url, stream=True)
                encrypted_data = response.content
                
                # AES Decryption (Key கிடைத்தால் மட்டும்)
                if aes_key and iv:
                    # 16-பைட் IV-ஐ உருவாக்குதல் (M3U8 லைப்ரரி சில நேரங்களில் IV-ஐ ஹெக்ஸ் ஸ்ட்ரிங்காகத் தரும்)
                    if isinstance(iv, str):
                        iv_bytes = bytes.fromhex(iv)
                    else:
                        iv_bytes = iv
                    
                    cipher = AES.new(aes_key, AES.MODE_CBC, iv_bytes)
                    decrypted_data = cipher.decrypt(encrypted_data)
                    # PKCS7 Padding-ஐ நீக்குதல்
                    try:
                        decrypted_data = unpad(decrypted_data, AES.block_size)
                    except ValueError:
                        # கடைசி சில துண்டுகளில் padding இல்லாமல் இருக்கலாம்
                        decrypted_data = decrypted_data
                    
                    with open(ts_path, 'wb') as f:
                        f.write(decrypted_data)
                else:
                    # Key இல்லை என்றால் encrypt செய்யாமல் நேரடியாக சேமித்தல்
                    with open(ts_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=1024):
                            f.write(chunk)
                            
                ts_files.append(ts_path)
            
            # FFmpeg மூலம் அனைத்து TS துண்டுகளையும் ஒன்றிணைத்து MP3 ஆக்குதல்
            output_file = "downloads/downloaded_audio.mp3"
            concat_list = "\n".join([f"file '{f}'" for f in ts_files])
            list_path = os.path.join(tmpdir, "concat_list.txt")
            with open(list_path, 'w') as f:
                f.write(concat_list)
            
            cmd = [
                'ffmpeg', '-f', 'concat', '-safe', '0',
                '-i', list_path, '-c', 'copy', output_file
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            
            logger.info(f"Successfully merged to {output_file}")
            return output_file
            
    except Exception as e:
        logger.error(f"Error in process_m3u8: {e}")
        raise Exception(f"Download failed: {e}")

# Main Function for Bot to call
def download(url):
    return asyncio.run(async_download(url))
