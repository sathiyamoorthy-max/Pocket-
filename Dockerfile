# அடிப்படை பைதான் சர்வரை எடுக்கிறோம்
FROM python:3.10-slim

# FFmpeg சாஃப்ட்வேரை சர்வரில் இன்ஸ்டால் செய்கிறோம்
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean

# வேலை செய்யும் இடத்தை உருவாக்குகிறோம்
WORKDIR /app

# தேவையான பைதான் பேக்கேஜ்களை இன்ஸ்டால் செய்கிறோம்
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# நமது பாட் ஸ்கிரிப்ட்டை காப்பி செய்கிறோம்
COPY . .

# பாட்டை ரன் செய்கிறோம் (உங்கள் பைதான் ஃபைல் பெயர் main.py எனில்)
CMD ["python", "main.py"]
