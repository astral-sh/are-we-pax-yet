# Are we pax yet? 📦

[Are we pax yet?](https://astral-sh.github.io/are-we-pax-yet/) tracks pax usage in PyPI
source distributions. It uses the design from [Are we PEP 740 yet?](https://github.com/trailofbits/are-we-pep740-yet).

The survey follows the 30-day download ranking from [Top PyPI Packages](https://hugovk.github.io/top-pypi-packages/).
The full results table includes every ranked project. The wheel and its JSON results cover the
first 360 projects whose latest release on PyPI has an unyanked sdist. The survey prefers the
`.tar.gz` if there are multiple formats and classifies ustar archives as pax.

## Development

```sh
uv run --locked --script generate.py
uv run --no-project python -m http.server --bind 127.0.0.1 --directory build 8000
```

Open [localhost:8000](http://localhost:8000/). Pass `--number 12` to generate a smaller sample.
The generator caches archive classifications by PyPI file URL and checks release metadata on each build.
Refresh the script lockfile with `uv lock --script generate.py` after changing its inline metadata.

```sh
uv run --locked ruff check
uv run --locked ruff format --check
```

GitHub Actions builds and deploys to Pages on pushes to `main` and once a day. The workflow can
also be run manually from the Actions tab.
