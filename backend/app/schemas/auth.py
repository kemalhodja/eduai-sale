import re
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=255)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value) -> str:
        return str(value).strip().lower()

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        if value.isdigit():
            raise ValueError("auth.password_too_weak")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value) -> str:
        return str(value).strip().lower()


class PinRequest(BaseModel):
    pin: str = Field(min_length=4, max_length=6)

    @field_validator("pin")
    @classmethod
    def pin_digits(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4,6}", value):
            raise ValueError("auth.pin_invalid_format")
        return value


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: UUID
    full_name: str
    biometric_enabled: bool
    has_pin: bool
    locale: str = "tr"
    timezone: str = "Europe/Istanbul"
    assistant_persona: str = "default"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=512)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        if value.isdigit():
            raise ValueError("auth.password_too_weak")
        return value


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class PinChangeRequest(BaseModel):
    current_pin: str = Field(min_length=4, max_length=6)
    new_pin: str = Field(min_length=4, max_length=6)

    @field_validator("current_pin", "new_pin")
    @classmethod
    def pin_digits(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4,6}", value):
            raise ValueError("auth.pin_invalid_format")
        return value


class UserProfile(BaseModel):
    id: UUID
    email: str
    full_name: str
    biometric_enabled: bool
    has_pin: bool
    locale: str = "tr"
    timezone: str = "Europe/Istanbul"
    assistant_persona: str = "default"


class PersonaRequest(BaseModel):
    assistant_persona: str = Field(max_length=32)


class LocaleRequest(BaseModel):
    locale: str


class TimezoneRequest(BaseModel):
    timezone: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value) -> str:
        return str(value).strip().lower()


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=512)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        if value.isdigit():
            raise ValueError("auth.password_too_weak")
        return value


class ForgotPasswordResponse(BaseModel):
    status: str = "ok"
    message: str
    reset_token: str | None = None
    # GUVENLIK/PRIVACY: production'da router bu alani None dondurur.
    # Gercek deger (True=adres mevcut + mail gitti) hesap varligini ele verir
    # (enumeration oracle). Yalnizca debug modunda doldurulur.
    email_sent: bool | None = None


class AdminClearPinRequest(BaseModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value) -> str:
        return str(value).strip().lower()
