# Maintainer: Hari Prakash
pkgname=dgpu-sleep-doctor
pkgver=1.0.0
pkgrel=1
pkgdesc="Diagnostics, auto-remediation, and zero-wake monitoring for NVIDIA hybrid graphics on Linux"
arch=('any')
url="https://github.com/hariprakash/dgpu-sleep-doctor"
license=('MIT')
depends=('python' 'python-setuptools')
optdepends=(
    'envycontrol: Alternative hybrid switcher'
    'mesa-utils: eglinfo/glxinfo for prime-run testing'
)
source=("$pkgname-$pkgver.tar.gz::https://github.com/hariprakash/$pkgname/archive/v$pkgver.tar.gz")
sha256sums=('SKIP')

build() {
    cd "$pkgname-$pkgver"
    python -m build --wheel --no-isolation
}

package() {
    cd "$pkgname-$pkgver"
    python -m installer --destdir="$pkgdir" dist/*.whl

    # Install prime-run
    install -Dm755 dgpu_doctor/remediate.py "$pkgdir/usr/share/$pkgname/remediate.py"

    # Install systemd user service
    install -Dm644 systemd/dgpu-sleep-guard.service "$pkgdir/usr/lib/systemd/user/dgpu-sleep-guard.service"

    # Install License
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
