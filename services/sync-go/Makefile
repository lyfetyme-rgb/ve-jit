BINARY ?= build/vejit-sync
GOARCH ?= amd64
VERSION ?= dev

.PHONY: test build release clean

test:
	go vet ./...
	go test -race -v ./...

build:
	@mkdir -p $(dir $(BINARY))
	go build -trimpath -o $(BINARY) ./cmd/vejit-sync

release:
	@mkdir -p $(dir $(BINARY))
	CGO_ENABLED=0 GOOS=linux GOARCH=$(GOARCH) \
		go build -trimpath -ldflags="-s -w -X main.version=$(VERSION)" \
		-o $(BINARY) ./cmd/vejit-sync

clean:
	rm -rf build
