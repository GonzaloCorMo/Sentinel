#!/usr/bin/env python3
"""Genera un set de secretos frescos para Supabase self-hosted.

Uso:
    python3 scripts/generate-supabase-keys.py            # imprime por stdout
    python3 scripts/generate-supabase-keys.py >> .env    # añade al .env
"""
import base64, hmac, hashlib, json, secrets, time


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwt_hs256(payload: dict, secret: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing = f"{header}.{body}".encode()
    sig = hmac.new(secret.encode(), signing, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url(sig)}"


def main() -> None:
    jwt_secret = secrets.token_urlsafe(48)
    now = int(time.time())
    exp = now + 10 * 365 * 24 * 3600  # 10 años

    anon = _jwt_hs256({"role": "anon", "iss": "supabase", "iat": now, "exp": exp}, jwt_secret)
    service = _jwt_hs256(
        {"role": "service_role", "iss": "supabase", "iat": now, "exp": exp}, jwt_secret
    )

    print(f"JWT_SECRET={jwt_secret}")
    print(f"SUPABASE_ANON_KEY={anon}")
    print(f"SUPABASE_SERVICE_ROLE_KEY={service}")
    print(f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}")
    print(f"DASHBOARD_PASSWORD={secrets.token_urlsafe(16)}")


if __name__ == "__main__":
    main()
