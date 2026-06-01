from fastapi import APIRouter, HTTPException, UploadFile, File

from ..llm import transcribe_audio

router = APIRouter()

@router.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    """Receive audio blob from browser, transcribe via OpenAI Whisper."""
    try:
        data = await audio.read()
        if len(data) < 1000:
            raise HTTPException(400, "Audio too short")
        text = await transcribe_audio(data, audio.content_type or "audio/webm")
        return {"transcript": text}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {e}")
