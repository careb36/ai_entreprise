Changed

* **The arm64 browser image is probed on arm64 hardware (issue #529).** `browser-image-probe.yml`, run only by hand, keeps building both architectures on the self-hosted runner and now runs the arm64 image with its probe on GitHub's arm64 runner (`ubuntu-24.04-arm`), with the daemon's sandbox flags. Under QEMU on the x64 runner the browser exited because its GPU process could not start, which said nothing about arm64 hardware. The new job holds no secret and a read-only token; `test_supply_chain.py` keeps each job on its runner.
