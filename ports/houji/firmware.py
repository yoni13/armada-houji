#!/usr/bin/env python3
"""Extract build inputs from the pinned public HyperOS archive, never a phone.

Large temporary images are deleted after extraction. No persist/NV partition,
account data, or per-device calibration is an input to this program.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

PORT = Path(__file__).resolve().parent


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--scratch', type=Path, help='Temporary directory with at least 20 GiB free')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    pin = json.loads((PORT / 'sources.json').read_text())['firmware']['sha256']
    if sha(args.archive) != pin:
        parser.error('Firmware does not match sources.json; use the documented archive')
    marker = output / 'firmware.json'
    if marker.exists():
        cached = json.loads(marker.read_text())
        if cached['archive_sha256'] == pin and all(
                (output / name).is_file() and sha(output / name) == value
                for name, value in cached['files'].items()):
            print('Verified cached firmware inputs')
            return
        parser.error('Firmware cache was modified; remove it and extract again')
    spec = importlib.util.spec_from_file_location('stock', PORT / 'inspect-stock.py')
    stock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stock)
    with tempfile.TemporaryDirectory(prefix='houji-firmware-', dir=args.scratch) as tmp:
        temp = Path(tmp)
        stock.inspect(args.archive, temp)
        raw = temp / 'super.raw'
        run('simg2img', temp / 'super.img', raw)
        (temp / 'super.img').unlink()
        run('lpunpack', '-p', 'vendor_a', '-p', 'odm_a', raw, temp)
        raw.unlink()

        def extract(partition, path, target, expected=None):
            dest = output / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            run('fsck.erofs', '--overwrite', '--extract=' + str(dest),
                '--path=' + path, temp / (partition + '_a.img'))
            if expected and sha(dest) != expected:
                raise ValueError('Unexpected stock firmware: ' + path)

        gpu = {
            'gmu_gen70900.bin': '9685f62c42543befcaee68d25355e16bf55cdb0d573bddcc9f6633f59eec3f72',
            'gen70900_sqe.fw': '8e2c5ce74140de7e161f14654b44de999855bce01b690fdd18c3358a73498cd1',
            'gen70900_aqe.fw': '25237c161d727b8ea92b4beb837a31651b44ea78bfc34d95672af8f346a45825',
        }
        for name, digest in gpu.items():
            extract('vendor', '/firmware/' + name, 'root/usr/lib/firmware/qcom/' + name, digest)
        extract('odm', '/firmware/gen70900_zap.mbn',
                'root/usr/lib/firmware/qcom/sm8650/xiaomi/houji/gen70900_zap.mbn',
                '53597ba96c8eb2725c71db5b3ebe4ee3d5b46499f72f4246dd0f55c1a79a0f80')
        for path, target, digest in [
            ('/lib64/libtouchreport.so', 'libtouchreport.so', '148c4bb19d8dcc3f0daba2a6d5730a22f2d4678430577cd6a3b797776ba0acd8'),
            ('/firmware/houji_syna_thp_config.ini', 'houji_syna_thp_config.ini', '16a1c0d411648e8bd8241e0a634a15a7aa92d298b1111c6bf689c154980c193b')]:
            extract('odm', path, 'root/usr/lib/armada/houji/touch/' + target, digest)
        extract('vendor', '/bin/batterysecret', 'root/usr/lib/armada/houji/charging/batterysecret',
                '4772868c56b49d8e8f8bd29044dba08151b70a7ed2d994cf4296245a481ecf5e')
        extract('odm', '/etc/sensors/config', 'root/usr/lib/armada/houji/sensors/config')
        extract('vendor', '/etc/sensors/sns_reg_config', 'root/usr/lib/armada/houji/sensors/sns_reg.conf')
        for side, digest in [('T', '03f862ba7dd720d046ec659e48552436b042e8ef654bf55c96059daf2a83016f'),
                             ('B', '9c2ee7b8fba1467f543608d8fce5b48e2b8606cf10ab9f6de4b85c6e62252ac2')]:
            stem = 'root/usr/lib/firmware/cirrus/cs35l41-dsp1-spk-prot-xiaomi-houji-' + side.lower()
            extract('odm', '/firmware/' + side + '-cs35l41-dsp1-spk-prot.bin', stem + '.bin', digest)
            extract('odm', '/firmware/cs35l41-dsp1-spk-prot.wmfw', stem + '.wmfw',
                    '1bb7ea70dd6b8e01ca5563cb051c025e1c8f361c57c28780ac559c0fc6b55f87')
        wifi = output / 'root/usr/lib/firmware/ath12k/WCN7850/hw2.0'
        wifi.mkdir(parents=True, exist_ok=True)
        for original, name, digest in [
            ('amss20.bin', 'amss.bin', '4db7b0e1aa7e4ec071708359c9d0db8d3d7872390cd8e88ffa0bbb30d560bcc8'),
            ('phy_ucode20.elf', 'm3.bin', '97c6accd597026a116a36ed2a167a1d229bd00d62653bbe381c2ec814cbbe3a7'),
            ('bd_n3gl.elf', 'board.bin', '6940a6ac689d724a173eb92bba9bf6f091632aa831ea39509b282af6c28ca126'),
            ('regdb_xiaomi.bin', 'regdb.bin', '237fc60976e5eb383ef0832e67d530d8b032e08e974fbe88a35f25c8c5784210')]:
            run('mcopy', '-o', '-i', temp / 'NON-HLOS.bin', '::/image/kiwi/' + original, wifi / name)
            if sha(wifi / name) != digest:
                raise ValueError('Unexpected Wi-Fi firmware: ' + original)
        reference = output / 'stock'
        reference.mkdir(exist_ok=True)
        for name in ('vbmeta.img',
                     'stock-report.json', 'stock-overlay.dtbo'):
            shutil.copy2(temp / name, reference / name)
        dtbo = (temp / 'dtbo.img').read_bytes()
        (reference / 'dtbo.img').write_bytes(dtbo[:struct.unpack_from('>I', dtbo, 4)[0]])
        report = json.loads((reference / 'stock-report.json').read_text())
        report.pop('archive', None)
        (reference / 'stock-report.json').write_text(json.dumps(report, indent=2) + '\n')
        for path in temp.glob('stock-*.dtb'):
            shutil.copy2(path, reference / path.name)
    records = {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob('*')) if p.is_file()}
    marker.write_text(json.dumps({'archive_sha256': pin, 'files': records}, indent=2) + '\n')
    print('Extracted firmware, board configuration and boot metadata; no device calibration included')


if __name__ == '__main__':
    main()
