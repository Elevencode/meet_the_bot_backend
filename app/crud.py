from sqlalchemy.orm import Session
from . import models

def get_user_by_google_sub(db: Session, google_sub: str):
    return db.query(models.User).filter(models.User.google_sub == google_sub).first()

def create_or_update_user(db: Session, user_info: dict, token: dict):
    db_user = get_user_by_google_sub(db, user_info['sub'])
    
    user_data = {
        "google_sub": user_info.get("sub"),
        "email": user_info.get("email"),
        "access_token": token.get("access_token"),
        "expires_at": token.get("expires_at"),
    }
    # Refresh token is only sent on the first authorization, so we only update it if we get a new one
    if "refresh_token" in token:
        user_data["refresh_token"] = token["refresh_token"]

    if db_user:
        # Update existing user
        for key, value in user_data.items():
            setattr(db_user, key, value)
    else:
        # Create new user
        db_user = models.User(**user_data)
        db.add(db_user)
    
    db.commit()
    db.refresh(db_user)
    return db_user

def update_user_token(db: Session, google_sub: str, new_token: dict):
    db_user = get_user_by_google_sub(db, google_sub)
    if db_user:
        db_user.access_token = new_token.get("access_token")
        db_user.expires_at = new_token.get("expires_at")
        db.commit()
        db.refresh(db_user)
    return db_user 