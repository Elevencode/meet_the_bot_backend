import time
from fastapi import FastAPI, Request, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from authlib.integrations.starlette_client import OAuth, OAuthError

from . import crud, models
from .database import engine, get_db
from .config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

models.Base.metadata.create_all(bind=engine)

app = FastAPI()
# app.add_middleware(SessionMiddleware, secret_key="your-secret-key") # No longer needed

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    access_token_params=None,
    authorize_params=None,
    api_base_url="https://www.googleapis.com/oauth2/v1/",
    client_kwargs={
        "scope": "openid email profile https://www.googleapis.com/auth/calendar.events",
        "redirect_uri": "http://localhost:8000/auth",
        "token_endpoint": "https://accounts.google.com/o/oauth2/token",
        "prompt": "consent",
        "access_type": "offline",
    },
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
)


class Event(BaseModel):
    summary: str
    description: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None

class EventCreateRequest(BaseModel):
    user_email: str
    event: Event

@app.get("/")
async def root():
    return {"message": "Hello Bot"}

@app.get("/login")
async def login(request: Request):
    url = request.url_for("auth")
    return await oauth.google.authorize_redirect(request, url)

@app.get("/auth")
async def auth(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        return {"message": "Error", "error": str(e)}
    
    user_info = await oauth.google.get('userinfo', token=token)
    user_info.raise_for_status()
    
    user = crud.create_or_update_user(db=db, user_info=user_info.json(), token=token)
    
    return {"message": "Authentication successful", "user_email": user.email}


async def get_valid_token(db: Session, user: models.User):
    if not user.expires_at or user.expires_at < time.time():
        # Token is expired, refresh it
        new_token = await oauth.google.fetch_access_token(
            refresh_token=user.refresh_token,
            grant_type='refresh_token'
        )
        crud.update_user_token(db=db, google_sub=user.google_sub, new_token=new_token)
        return new_token
    
    # Token is still valid, construct the token dict for the API call
    return {
        "access_token": user.access_token,
        "token_type": "Bearer",
        "expires_in": int(user.expires_at - time.time()),
        "scope": "openid email profile https://www.googleapis.com/auth/calendar.events",
    }


@app.post("/create-event")
async def create_event(req: EventCreateRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == req.user_email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please login first.")
    
    if not user.refresh_token:
        raise HTTPException(status_code=400, detail="User has no refresh token. Please re-authenticate with prompt=consent.")

    token = await get_valid_token(db, user)
    
    event = req.event
    start_time = event.start_time or datetime.utcnow()
    end_time = event.end_time or start_time + timedelta(hours=1)

    event_data = {
        "summary": event.summary,
        "description": event.description,
        "start": {"dateTime": start_time.isoformat() + "Z", "timeZone": "UTC"},
        "end": {"dateTime": end_time.isoformat() + "Z", "timeZone": "UTC"},
        "conferenceData": {
            "createRequest": {
                "requestId": f"meet-the-bot-{start_time.timestamp()}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }

    resp = await oauth.google.post(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events?conferenceDataVersion=1",
        json=event_data,
        token=token,
    )
    
    if resp.status_code >= 300:
        raise HTTPException(status_code=resp.status_code, detail=resp.json())

    return resp.json()
