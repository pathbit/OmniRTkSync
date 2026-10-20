"""enc:v1 — interop de cifra com o armazenamento do OmniRoute.

O vetor INTEROP foi gerado com o scryptSync + AES-256-GCM do Node.js (os
mesmos parâmetros do bundle do OmniRoute) contra a chave de teste — a prova
de que o Python lê exatamente o que o gateway escreve.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from omni_rtksync import gateway
from omni_rtksync.gateway import _aes, decrypt, decrypt_if_needed, encrypt, is_encrypted

CHAVE = "teste-chave-de-campo-32bytes"
INTEROP_NODE = "enc:v1:b321265c13c4321569e5def33f2e5e46:0101a8fc1b2ae325092ffed20fdfcd0920abb6e9:573d1423166eee97e8eab80aa4851b5a"
SEGREDO = "sk-teste-segredo-123"

# A pycryptodome e dependencia declarada do pacote; ela so falta num ambiente
# que roda o codigo-fonte sem instalar nada. Ai o modulo degrada (devolve o
# valor cifrado intacto) e estes testes se declaram pulados, em vez de falhar.
COM_AES = _aes() is not None


@unittest.skipUnless(COM_AES, "pycryptodome nao instalada")
class EncV1InteropTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["STORAGE_ENCRYPTION_KEY"] = CHAVE

    def tearDown(self) -> None:
        os.environ.pop("STORAGE_ENCRYPTION_KEY", None)

    def test_decifra_valor_produzido_pelo_node(self) -> None:
        self.assertEqual(decrypt(INTEROP_NODE), SEGREDO)

    def test_round_trip_cifra_e_decifra(self) -> None:
        cifrado = encrypt(SEGREDO)
        self.assertTrue(is_encrypted(cifrado))
        self.assertEqual(decrypt(cifrado), SEGREDO)

    def test_cifras_sao_unicas_por_iv_aleatorio(self) -> None:
        self.assertNotEqual(encrypt("mesmo"), encrypt("mesmo"))

    def test_sem_chave_passthrough_e_none(self) -> None:
        os.environ.pop("STORAGE_ENCRYPTION_KEY", None)
        self.assertIsNone(decrypt(INTEROP_NODE))
        self.assertEqual(encrypt("claro"), "claro")

    def test_valor_em_claro_passa_intacto(self) -> None:
        self.assertEqual(decrypt("texto-claro"), "texto-claro")
        self.assertEqual(decrypt_if_needed("texto-claro"), "texto-claro")

    def test_malformado_nao_levanta(self) -> None:
        self.assertIsNone(decrypt("enc:v1:aa:bb"))
        self.assertEqual(decrypt_if_needed("enc:v1:aa:bb"), "enc:v1:aa:bb")

    def test_tag_adulterada_recusa(self) -> None:
        adulterado = INTEROP_NODE[:-1] + ("0" if INTEROP_NODE[-1] != "0" else "1")
        self.assertIsNone(decrypt(adulterado))


class SemBibliotecaTest(unittest.TestCase):
    """Sem a pycryptodome o modulo degrada para o comportamento anterior."""

    def setUp(self) -> None:
        os.environ["STORAGE_ENCRYPTION_KEY"] = CHAVE

    def tearDown(self) -> None:
        os.environ.pop("STORAGE_ENCRYPTION_KEY", None)

    def test_decifrar_devolve_none_e_o_atalho_devolve_o_original(self) -> None:
        with mock.patch("omni_rtksync.gateway._aes", return_value=None):
            self.assertIsNone(decrypt(INTEROP_NODE))
            self.assertEqual(decrypt_if_needed(INTEROP_NODE), INTEROP_NODE)

    def test_cifrar_faz_passthrough(self) -> None:
        with mock.patch("omni_rtksync.gateway._aes", return_value=None):
            self.assertEqual(encrypt("claro"), "claro")


@unittest.skipUnless(COM_AES, "pycryptodome nao instalada")
class IntegracaoComOBancoDoGatewayTest(unittest.TestCase):
    """Leitura decifra e escrita re-cifra, contra um SQLite real."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "storage.sqlite")
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "CREATE TABLE provider_connections (id TEXT PRIMARY KEY, provider TEXT, name TEXT, "
                "access_token TEXT, refresh_token TEXT, api_key TEXT, expires_at TEXT, "
                "test_status TEXT, created_at TEXT, updated_at TEXT)"
            )
            conn.execute(
                "INSERT INTO provider_connections VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("c1", "antigravity", "Conta", INTEROP_NODE, None, None,
                 "2030-01-01T00:00:00Z", "active", "2026-01-01", "2026-01-01"),
            )
        os.environ["STORAGE_ENCRYPTION_KEY"] = CHAVE

    def tearDown(self) -> None:
        os.environ.pop("STORAGE_ENCRYPTION_KEY", None)
        self.dir.cleanup()

    def test_a_leitura_entrega_o_valor_decifrado(self) -> None:
        (conexao,) = gateway.get_all_connections(self.db)
        self.assertEqual(conexao["accessToken"], SEGREDO)

    def test_sem_a_chave_a_leitura_devolve_o_cifrado_intacto(self) -> None:
        os.environ.pop("STORAGE_ENCRYPTION_KEY", None)
        (conexao,) = gateway.get_all_connections(self.db)
        self.assertEqual(conexao["accessToken"], INTEROP_NODE)

    def test_a_escrita_regrava_cifrada_e_a_leitura_fecha_o_ciclo(self) -> None:
        gateway.update_connection(self.db, "c1", "novo-access", "novo-refresh", 1893456000000)
        with sqlite3.connect(self.db) as conn:
            bruto_a, bruto_r = conn.execute(
                "SELECT access_token, refresh_token FROM provider_connections WHERE id='c1'"
            ).fetchone()
        self.assertTrue(is_encrypted(bruto_a) and is_encrypted(bruto_r))
        self.assertNotIn("novo-access", bruto_a)
        (conexao,) = gateway.get_all_connections(self.db)
        self.assertEqual(conexao["accessToken"], "novo-access")
        self.assertEqual(conexao["refreshToken"], "novo-refresh")


if __name__ == "__main__":
    unittest.main()
