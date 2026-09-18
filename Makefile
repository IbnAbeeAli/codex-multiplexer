.PHONY: test verify install

test:
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

verify:
	PYTHONDONTWRITEBYTECODE=1 python3 scripts/verify.py

install:
	./install.sh
