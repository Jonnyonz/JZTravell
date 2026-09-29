"""Test de la migracion transparente de bcrypt a Argon2id (seccion 3.4 de la
hoja de ruta). No requiere base de datos."""

import bcrypt

from database import hash_password, needs_rehash, verify_password_any


def test_verify_password_any_accepts_legacy_bcrypt_hash():
    stored = bcrypt.hashpw(b"Clave-Segura123!", bcrypt.gensalt()).decode()
    assert verify_password_any("Clave-Segura123!", stored)
    assert not verify_password_any("otra-clave", stored)


def test_verify_password_any_accepts_argon2id_hash():
    stored = hash_password("Clave-Segura123!")
    assert verify_password_any("Clave-Segura123!", stored)
    assert not verify_password_any("otra-clave", stored)


def test_fresh_argon2id_hash_does_not_need_rehash():
    stored = hash_password("Clave-Segura123!")
    assert needs_rehash(stored) is False
