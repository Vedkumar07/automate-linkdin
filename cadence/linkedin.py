"""LinkedIn through the official API only: OAuth sign-in, then text or text-with-photo posts to your own profile."""
from __future__ import annotations

import urllib.parse
from datetime import timedelta
from pathlib import Path

from . import quality
from .config import Settings
from .db import DB
from .util import HttpError, http, iso, parse_iso, utcnow

SCOPES = "openid profile w_member_social"


class LinkedInError(Exception):
    pass


class LinkedIn:
    def __init__(self, st: Settings, db: DB):
        self.st, self.db = st, db
        self.api = st.secret("LINKEDIN_API_BASE") or "https://api.linkedin.com"
        self.auth_base = st.secret("LINKEDIN_AUTH_BASE") or "https://www.linkedin.com"

    # ---- sign-in ----
    @property
    def redirect_uri(self) -> str:
        w = self.st["web"]
        return self.st.secret("LINKEDIN_REDIRECT_URI") or f"http://localhost:{w['port']}/oauth/callback"

    def configured(self) -> bool:
        return bool(self.st.secret("LINKEDIN_CLIENT_ID") and self.st.secret("LINKEDIN_CLIENT_SECRET"))

    def auth_url(self, state: str) -> str:
        q = urllib.parse.urlencode({
            "response_type": "code", "client_id": self.st.secret("LINKEDIN_CLIENT_ID"),
            "redirect_uri": self.redirect_uri, "state": state, "scope": SCOPES})
        return f"{self.auth_base}/oauth/v2/authorization?{q}"

    def exchange(self, code: str) -> dict:
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
            "client_id": self.st.secret("LINKEDIN_CLIENT_ID"), "client_secret": self.st.secret("LINKEDIN_CLIENT_SECRET"),
        }).encode()
        try:
            tok = http("POST", f"{self.auth_base}/oauth/v2/accessToken", data=body, retries=0, timeout=30,
                       headers={"Content-Type": "application/x-www-form-urlencoded"}).json()
        except HttpError as e:
            raise LinkedInError(f"LinkedIn rejected the sign-in: {e.body[:200]}") from e
        expires = utcnow() + timedelta(seconds=int(tok.get("expires_in", 5184000)))
        data = {"access_token": tok["access_token"], "expires_at": iso(expires)}
        me = self._get("/v2/userinfo", data["access_token"])
        data.update(sub=me.get("sub", ""), name=me.get("name", ""))
        self.db.kv_set("linkedin_token", data)
        return data

    def token(self) -> dict | None:
        return self.db.kv_get("linkedin_token")

    def status(self) -> dict:
        t = self.token()
        if not t:
            return {"connected": False, "note": "not signed in"}
        days = (parse_iso(t["expires_at"]) - utcnow()).total_seconds() / 86400
        if days <= 0:
            return {"connected": False, "note": "sign-in expired", "name": t.get("name", "")}
        return {"connected": True, "name": t.get("name", ""), "days_left": int(days),
                "note": f"signed in as {t.get('name', 'you')}, expires in {int(days)} days"}

    # ---- calls ----
    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "X-Restli-Protocol-Version": "2.0.0",
                "LinkedIn-Version": self.st["linkedinVersion"]}

    def _get(self, path: str, token: str) -> dict:
        return http("GET", f"{self.api}{path}", headers=self._headers(token), timeout=30).json()

    def _need_token(self) -> dict:
        s = self.status()
        if not s["connected"]:
            raise LinkedInError(f"LinkedIn is not connected ({s['note']}). Open the dashboard and press Connect LinkedIn.")
        return self.token()

    def upload_image(self, t: dict, path: Path) -> str:
        owner = f"urn:li:person:{t['sub']}"
        try:
            init = http("POST", f"{self.api}/rest/images?action=initializeUpload", headers=self._headers(t["access_token"]),
                        json_body={"initializeUploadRequest": {"owner": owner}}, retries=1).json()["value"]
            http("PUT", init["uploadUrl"], data=path.read_bytes(), timeout=120, retries=1,
                 headers={"Authorization": f"Bearer {t['access_token']}", "Content-Type": "application/octet-stream"})
        except (HttpError, KeyError) as e:
            raise LinkedInError(f"Photo upload failed: {e}") from e
        return init["image"]

    def upload_document(self, t: dict, path: Path) -> str:
        """Uploads a PDF for a LinkedIn document (carousel) post. Returns the document URN."""
        owner = f"urn:li:person:{t['sub']}"
        try:
            init = http("POST", f"{self.api}/rest/documents?action=initializeUpload", headers=self._headers(t["access_token"]),
                        json_body={"initializeUploadRequest": {"owner": owner}}, retries=1).json()["value"]
            http("PUT", init["uploadUrl"], data=path.read_bytes(), timeout=180, retries=1,
                 headers={"Authorization": f"Bearer {t['access_token']}", "Content-Type": "application/pdf"})
        except (HttpError, KeyError) as e:
            raise LinkedInError(f"Document upload failed: {e}") from e
        return init["document"]

    def post(self, text: str, photo: str = "", alt: str = "", document: str = "", doc_title: str = "") -> str:
        """Publishes to your own profile. Returns the post id. Never retried automatically, to avoid duplicates."""
        t = self._need_token()
        body = {
            "author": f"urn:li:person:{t['sub']}",
            "commentary": quality.little_text(text),
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if photo and Path(photo).exists():
            body["content"] = {"media": {"id": self.upload_image(t, Path(photo)), "altText": (alt or self.st["photoAltText"])[:300]}}
        if document and Path(document).exists():
            body["content"] = {"media": {"id": self.upload_document(t, Path(document)), "title": (doc_title or "Carousel")[:120]}}
        try:
            r = http("POST", f"{self.api}/rest/posts", headers=self._headers(t["access_token"]), json_body=body,
                     retries=0, timeout=60)
        except HttpError as e:
            if e.status == 401:
                self.db.kv_set("linkedin_token", None)
                raise LinkedInError("LinkedIn says the sign-in is no longer valid. Press Connect LinkedIn again.") from e
            if e.status == 429:
                raise LinkedInError("LinkedIn rate limit hit. Try again later.") from e
            raise LinkedInError(f"LinkedIn refused the post ({e.status}): {e.body[:250]}") from e
        return r.headers.get("x-restli-id", "")
