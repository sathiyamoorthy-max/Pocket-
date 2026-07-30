import yt_dlp
import os
import logging

logger = logging.getLogger(__name__)

def download(url):
    try:
        # உங்கள் ப்ராஜெக்ட்டில் 'downloads' என்ற ஃபோல்டர் இல்லை என்றால் அதை உருவாக்கும்
        if not os.path.exists('downloads'):
            os.makedirs('downloads')

        # yt-dlp-க்கான செட்டிங்ஸ் (ஆடியோ மட்டும் எடுத்து MP3-ல் சேமிக்க)
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'downloads/%(title)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # லிங்க்-ஐ பார்த்து ஆடியோவை டவுன்லோட் செய்யும்
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            # கோப்பின் பெயரை MP3 ஆக மாற்றும்
            base, _ = os.path.splitext(filename)
            final_filename = f"{base}.mp3"
            
            logger.info(f"Downloaded file: {final_filename}")
            return final_filename

    except Exception as e:
        logger.error(f"Error in pocketfm_dl: {e}")
        raise Exception(f"Download failed: {e}")
