PROJECT=frisch
REPO=registry.rc.nectar.org.au/nectar
DESCRIBE=$(shell git describe --tags)
# PEP 440-safe package version for setuptools-scm. Docker tags can't contain '+',
# so the image tag keeps the raw `git describe` form while the package version
# rewrites `<tag>-<n>-g<sha>` into the local-version form `<tag>+<n>.g<sha>`.
VERSION := $(shell git describe --tags | sed -e 's/-/+/' -e 's/-/./g')
IMAGE_TAG := $(if $(TAG),$(TAG),$(DESCRIBE))
IMAGE=$(REPO)/$(PROJECT):$(IMAGE_TAG)
BUILDER=docker
BUILDER_ARGS=
build:
	echo "Derived image tag: $(DESCRIBE)"
	echo "Actual image tag: $(IMAGE_TAG)"
	echo "Package version: $(VERSION)"
	$(BUILDER) build $(BUILDER_ARGS) --build-arg VERSION=$(VERSION) -t $(IMAGE) .
push:
	$(BUILDER) push $(IMAGE)
.PHONY: build push
