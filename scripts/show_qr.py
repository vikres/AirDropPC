"""Prints the PC address and token as text and as QR codes, for Install.cmd.
Usage: python show_qr.py <address> <token>"""
import sys

import qrcode

for label, value in (("PC address", sys.argv[1]), ("Token", sys.argv[2])):
    print(f"\n{label}: {value}")
    qr = qrcode.QRCode(border=2)
    qr.add_data(value)
    qr.print_ascii(invert=True)
