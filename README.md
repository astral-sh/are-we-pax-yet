# Are we PAX yet? 📦

[Are we PAX yet?](https://astral-sh.github.io/are-we-pax-yet/) tracks PAX usage in PyPI
source distributions. It uses the design from [Are we PEP 740 yet?](https://github.com/trailofbits/are-we-pep740-yet).

The survey follows the 30-day download ranking from [Top PyPI Packages](https://hugovk.github.io/top-pypi-packages/).
It charts the first 360 projects whose latest release on PyPI has an unyanked sdist, preferring
the `.tar.gz` if there are multiple formats. The wheel counts both PAX and plain ustar headers
as PAX-compatible. The JSON results record the specific format.

## Development

```sh
uv run --script generate.py
uv run --no-project python -m http.server --bind 127.0.0.1 --directory build 8000
```

Open [localhost:8000](http://localhost:8000/). Pass `--number 12` to generate a smaller sample.

```sh
uvx ruff@0.17.3 check
uvx ruff@0.17.3 format --check
uv run --no-project python -m unittest
```

GitHub Actions builds and deploys to Pages on pushes to `main` and once a day. The workflow can
also be run manually from the Actions tab.
