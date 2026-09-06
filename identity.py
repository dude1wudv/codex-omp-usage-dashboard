"""Stable local identity distinguishes Team members sharing a workspace ID."""
import base64,hashlib,json

def stable_id(account,email):
    return hashlib.sha256((account+'|'+email.strip().lower()).encode()).hexdigest()[:16] if account and email else None

def from_token(raw,token):
    try:
        encoded=token.split('.')[1]
        payload=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
        email=(payload.get('https://api.openai.com/profile') or {}).get('email','')
        # Used only as local identity metadata, never as authentication validation.
        aid=stable_id(raw,email)
        return {'id':aid,'raw':raw,'token':token}
    except (ValueError,IndexError,TypeError,AttributeError):return {}
