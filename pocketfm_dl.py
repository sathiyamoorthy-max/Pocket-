import os
import re
import subprocess
import logging
import tempfile
import requests
import m3u8
import asyncio
import uuid
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

logger = logging.getLogger(__name__)

def load_cookies(cookie_file='cookies.txt'):
    cookies = {}
    if os.path.exists(cookie_file):
        with open(cookie_file, 'r') as f:
            for line in f:
                if not line.strip() or line.startswith('#'):
                    continue
                parts = line.strip().split('\t')
                if len(parts) >= 7:
                    cookies[parts[5]] = parts[6]
        logger.info("Cookies loaded successfully!")
    else:
        logger.warning("cookies.txt file not found! Download might fail.")
    return cookies

def get_episode_metadata_and_m3u8(episode_url, session):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Referer': 'https://pocketfm.com/'
    }
    logger.info(f"Fetching episode page metadata: {episode_url}")
    
    response = session.get(episode_url, headers=headers, timeout=20)
    html_content = response.text
    
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html_content)
    episode_title = title_match.group(1) if title_match else "Pocket FM Audio"
    
    img_match = re.search(r'<meta property="og:image" content="([^"]+)"', html_content)
    thumbnail_url = img_match.group(1) if img_match else None
    
    m3u8_match = re.search(r'(https?://[^\s"\'<>]+\.cloudfront\.net[^\s"\'<>]*?\.m3u8[^\s"\'<>]*)', html_content)
    if not m3u8_match:
        m3u8_match = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html_content)
        
    if m3u8_match:
        clean_url = m3u8_match.group(1).replace('\\/', '/').replace('\\u002F', '/')
        return clean_url, episode_title, thumbnail_url
    else:
        raise Exception("M3U8 URL not found! Please check if cookies.txt is valid.")

def process_m3u8(m3u8_url, session):
    try:
        playlist = m3u8.load(m3u8_url)
        base_url = '/'.join(m3u8_url.split('/')[:-1])
        
        if not playlist.segments and playlist.playlists:
            child_uri = playlist.playlists[0].uri
            child_url = child_uri if child_uri.startswith('http') else f"{base_url}/{child_uri}"
            playlist = m3u8.load(child_url)
            base_url = '/'.join(child_url.split('/')[:-1])
        
        key_uri, iv = None, None
        if playlist.keys:
            key_obj = playlist.keys[0]
            if key_obj and key_obj.uri:
                key_uri = key_obj.uri
                iv = key_obj.iv

        aes_key = None
        if key_uri:
            key_url = key_uri if key_uri.startswith('http') else f"{base_url}/{key_uri}"
            response = session.get(key_url, timeout=20)
            aes_key = response.content

        segment_urls = [seg.uri for seg in playlist.segments]
        if not segment_urls:
            raise Exception("No audio segments found.")
        
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
            
        with tempfile.TemporaryDirectory() as tmpdir:
            ts_files = []
            
            for i, seg_url in enumerate(segment_urls):
                seg_url = seg_url if seg_url.startswith('http') else f"{base_url}/{seg_url}"
                ts_path = os.path.join(tmpdir, f"chunk_{i:04d}.ts")
                
                response = session.get(seg_url, stream=True, timeout=20)
                encrypted_data = response.content
                
                if aes_key and iv:
                    iv_bytes = bytes.fromhex(iv.replace('0x', '')) if isinstance(iv, str) else iv
                    if len(iv_bytes) < 16:
                        iv_bytes = iv_bytes.ljust(16, b'\0')
                    cipher = AES.new(aes_key, AES.MODE_CBC, iv_bytes)
                    decrypted_data = cipher.decrypt(encrypted_data)
                    try:
                        decrypted_data = unpad(decrypted_data,.AES.block_size)
                    except ValueError:
                        pass
                    with open(ts_path, 'wb') as f:
                        f.write(decrypted_data)
                else:
                    with open(ts_path, 'wb') as f:
                        f.write(encrypted_data)
                        
                ts_files.append(ts_path)
            
            # Unique ID பயன்படுத்தி மோதல் (Conflict) ஏற்படுவதைத் தவிர்த்தல்
            unique_name = str(uuid.uuid4())[:8]
            output_file = f"downloads/audio_{unique_name}.mp3"
            list_path = os.path.join(tmpdir, "concat_list.txt")
            with open(list_path, 'w') as f:
                for f_path in ts_files:
                    f.write(f"file '{f_path}'\n")
            
            cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file]
            subprocess.run(cmd, check=True, capture_output=True)
            
            return output_file
            
    except Exception as e:
        logger.error(f"Error in process_m3u8: {e}")
        raise Exception(f"Download failed: {e}")

def download(url):
    session = requests.Session()
    session.cookies.update(load_cookies())
    m3u8_url, title, thumb_url = get_episode_metadata_and_m3u8(url, session)
    audio_file = process_m3u8(m3u8_url, session)
    return audio_file, title, thumb_url
