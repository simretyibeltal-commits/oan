"""
Production-Ready TTS Provider
"""

import os
from typing import AsyncGenerator, Optional
from helpers.utils import get_logger
import re

logger = get_logger(__name__)


def convert_numbers_to_words(text: str, lang: str) -> str:
    """
    Convert numbers in text to words for better TTS pronunciation.
    
    Args:
        text: Text containing numbers
        lang: Language code ('en' or 'am')
    
    Returns:
        Text with numbers converted to words
    """
    if lang == 'en':
        # English number conversion (basic implementation)
        def num_to_words_en(n):
            if n == 0:
                return 'zero'
            
            ones = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']
            teens = ['ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 
                    'sixteen', 'seventeen', 'eighteen', 'nineteen']
            tens = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']
            
            if n < 10:
                return ones[n]
            elif n < 20:
                return teens[n - 10]
            elif n < 100:
                return tens[n // 10] + ('' if n % 10 == 0 else ' ' + ones[n % 10])
            elif n < 1000:
                return ones[n // 100] + ' hundred' + ('' if n % 100 == 0 else ' ' + num_to_words_en(n % 100))
            elif n < 1000000:
                return num_to_words_en(n // 1000) + ' thousand' + ('' if n % 1000 == 0 else ' ' + num_to_words_en(n % 1000))
            else:
                return str(n)  # Fallback for very large numbers
        
        # Replace numbers with words
        def replace_num(match):
            num_str = match.group(0).replace(',', '')
            try:
                num = int(num_str)
                return num_to_words_en(num)
            except:
                return match.group(0)
        
        text = re.sub(r'\b\d{1,3}(?:,\d{3})*\b', replace_num, text)
        
    elif lang == 'am':
        # Amharic number conversion
        def num_to_words_am(n):
            if n == 0:
                return 'ዜሮ'
            
            ones = ['', 'አንድ', 'ሁለት', 'ሦስት', 'አራት', 'አምስት', 'ስድስት', 'ሰባት', 'ስምንት', 'ዘጠኝ']
            tens = ['', 'አስር', 'ሃያ', 'ሰላሳ', 'አርባ', 'ሃምሳ', 'ስልሳ', 'ሰባ', 'ሰማንያ', 'ዘጠና']
            
            if n < 10:
                return ones[n]
            elif n == 10:
                return 'አስር'
            elif n < 20:
                return 'አስራ ' + ones[n - 10]
            elif n < 100:
                return tens[n // 10] + ('' if n % 10 == 0 else ' ' + ones[n % 10])
            elif n < 1000:
                return ones[n // 100] + ' መቶ' + ('' if n % 100 == 0 else ' ' + num_to_words_am(n % 100))
            elif n < 1000000:
                return num_to_words_am(n // 1000) + ' ሺህ' + ('' if n % 1000 == 0 else ' ' + num_to_words_am(n % 1000))
            else:
                return str(n)  # Fallback
        
        # Replace numbers with Amharic words
        def replace_num(match):
            num_str = match.group(0).replace(',', '')
            try:
                num = int(num_str)
                return num_to_words_am(num)
            except:
                return match.group(0)
        
        text = re.sub(r'\b\d{1,3}(?:,\d{3})*\b', replace_num, text)
    
    return text


class TTSProvider:
    """Abstract base class for TTS providers"""

    async def stream_audio(
        self,
        text_stream: AsyncGenerator[str, None],
        lang: str = "en"
    ) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError


class CoquiXTTSProvider(TTSProvider):
    """TTS using Coqui XTTS-v2 server (xtts-api-server /tts_stream endpoint)."""

    def __init__(self, base_url: str = None):
        self.base_url = (base_url or os.getenv("XTTS_URL", "http://localhost:8020")).rstrip('/')
        self.speaker_wav = os.getenv("XTTS_SPEAKER_WAV", "")
        logger.info(f"✅ Coqui XTTS Provider initialized: {self.base_url}")

    async def stream_audio(
        self,
        text_stream: AsyncGenerator[str, None],
        lang: str = "en"
    ) -> AsyncGenerator[bytes, None]:
        import httpx
        lang_code = "en" if lang.startswith("en") else "am"
        buffer = ""
        delimiters = {".", "!", "?", ";", "\n", ","}

        async def synthesize_chunk(text: str):
            text = convert_numbers_to_words(text.strip(), lang)
            if not text:
                return
            payload = {"text": text, "language": lang_code, "speaker_wav": self.speaker_wav}
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    async with client.stream("POST", f"{self.base_url}/tts_stream", json=payload) as resp:
                        resp.raise_for_status()
                        header_stripped = False
                        wav_buf = b''
                        async for chunk in resp.aiter_bytes(8192):
                            if not chunk:
                                continue
                            wav_buf += chunk
                            if not header_stripped and len(wav_buf) >= 44:
                                wav_buf = wav_buf[44:]
                                header_stripped = True
                            if header_stripped and wav_buf:
                                yield wav_buf
                                wav_buf = b''
            except Exception as e:
                logger.error(f"XTTS streaming error: {e}")

        async for text_chunk in text_stream:
            if not text_chunk:
                continue
            buffer += text_chunk
            if any(c in delimiters for c in text_chunk):
                split_idx = max((i for i, c in enumerate(buffer) if c in delimiters), default=-1)
                if split_idx != -1:
                    to_synth = buffer[:split_idx + 1].strip()
                    buffer = buffer[split_idx + 1:].strip()
                    if to_synth:
                        async for audio_bytes in synthesize_chunk(to_synth):
                            yield audio_bytes
            elif len(buffer) > 80:
                async for audio_bytes in synthesize_chunk(buffer):
                    yield audio_bytes
                buffer = ""

        if buffer.strip():
            async for audio_bytes in synthesize_chunk(buffer):
                yield audio_bytes


# Singleton
_tts_provider: Optional[TTSProvider] = None


def get_tts_provider() -> TTSProvider:
    """Get TTS provider based on configuration"""
    global _tts_provider
    if _tts_provider is None:
        _tts_provider = CoquiXTTSProvider()
        logger.info("TTS Provider initialized: coqui_xtts")

    return _tts_provider


def cleanup_tts_provider():
    """Cleanup TTS provider resources"""
    global _tts_provider
    if _tts_provider is not None and hasattr(_tts_provider, 'cleanup'):
        _tts_provider.cleanup()
        logger.info("TTS Provider cleaned up")
