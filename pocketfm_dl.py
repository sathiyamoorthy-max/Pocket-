import yt_dlp
import os
import logging

logger = logging.getLogger(__name__)

def download(url):
    try:
        if not os.path.exists('downloads'):
            os.makedirs('downloads')

        cookie_file = 'cookies.txt'
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
            'extractor_args': {'generic': {'no-playlist': ['true']}},
            # Render IP Block-ஐ தவிர்க்க Proxy சேர்த்துள்ளேன்.
            # இங்கே உண்மையான வேலை செய்யும் Proxy IP-யை போட வேண்டும்.
            # (இலவச Proxy-கள் மெதுவாக இருக்கும், சோதனைக்காக கீழே உள்ளதை பயன்படுத்தலாம். இது வேலை செய்யவில்லை என்றால், 'proxy': None என்று வைத்துவிடுங்கள்)
            'proxy': None 
        }

        if os.path.exists(cookie_file):
            ydl_opts['cookiefile'] = cookie_file
            logger.info("Using cookies to bypass login.")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            base, _ = os.path.splitext(filename)
            final_filename = f"{base}.mp3"
            
            logger.info(f"Downloaded file: {final_filename}")
            return final_filename

    except Exception as e:
        logger.error(f"Error in pocketfm_dl: {e}")
        raise Exception(f"Download failed: {e}")
