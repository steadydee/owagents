import unittest

from tools.registro_compliance.server import mrz_check_digit, parse_td1, parse_td3


class MrzTests(unittest.TestCase):
    def test_td3_sample_checksums_pass(self):
        doc = parse_td3([
            "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
            "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
        ])

        self.assertEqual(doc["docType"], "P")
        self.assertEqual(doc["docNumber"], "L898902C3")
        self.assertEqual(doc["nationalityIso"], "UTO")
        self.assertEqual(doc["fechaNacimiento"], "1974-08-12")
        self.assertEqual(doc["sexo"], "F")
        self.assertEqual(doc["docExpiry"], "2012-04-15")
        self.assertEqual(doc["primerApellido"], "ERIKSSON")
        self.assertEqual(doc["nombres"], "ANNA MARIA")
        self.assertTrue(doc["mrzChecksumsOk"])
        self.assertTrue(all(doc["checks"].values()))

    def test_td3_bad_checksum_is_reported(self):
        doc = parse_td3([
            "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
            "L898902C36UTO7408122F1204159ZE184226B<<<<<11",
        ])

        self.assertFalse(doc["mrzChecksumsOk"])
        self.assertFalse(doc["checks"]["composite"])

    def test_check_digit_uses_mrz_weights(self):
        self.assertEqual(mrz_check_digit("L898902C3"), "6")
        self.assertEqual(mrz_check_digit("740812"), "2")
        self.assertEqual(mrz_check_digit("120415"), "9")

    def test_td1_sample_checksums_pass(self):
        doc = parse_td1([
            "I<UTOD231458907<<<<<<<<<<<<<<<",
            "7408122F1204159UTO<<<<<<<<<<<6",
            "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
        ])

        self.assertEqual(doc["docType"], "I")
        self.assertEqual(doc["docNumber"], "D23145890")
        self.assertEqual(doc["nationalityIso"], "UTO")
        self.assertEqual(doc["fechaNacimiento"], "1974-08-12")
        self.assertEqual(doc["sexo"], "F")
        self.assertEqual(doc["docExpiry"], "2012-04-15")
        self.assertEqual(doc["primerApellido"], "ERIKSSON")
        self.assertEqual(doc["nombres"], "ANNA MARIA")
        self.assertTrue(doc["mrzChecksumsOk"])


if __name__ == "__main__":
    unittest.main()
