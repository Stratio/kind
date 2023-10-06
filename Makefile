package:
	make build && bin/package.sh $(version)

deploy:
	bin/deploy.sh $(version)

acceptance-test:
	python3 bin/at.py

change-version:
	bin/change-version.sh $(version)