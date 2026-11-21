# Postroom

Company login, then one **Generate** button. The caption comes from the saved brand profile (CoralBricks or OpenAI). The image comes from OpenAI. Post both to the connected personal LinkedIn profile.

## Setup

1. Copy `.env.example` to `.env`.
2. Set `CORAL_API_KEY` and `OPENAI_API_KEY` separately. The Coral key is not an OpenAI key.
3. Set `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, and `SESSION_SECRET`.
4. In the LinkedIn app, the redirect URL is `http://localhost:8000/auth/callback`.
5. Install and run:

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000, register the company, then use Studio.
