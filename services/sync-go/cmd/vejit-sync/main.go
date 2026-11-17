// vejit-sync — stateless replay classifier for the Vendarix edge node.
//
// Reads a snapshot of pending outbound deltas (JSON lines, one delta per line)
// plus a baseline file mapping SKU -> on-hand at the last synced watermark, and
// emits one verdict per delta as JSON lines.
//
// The worker NEVER writes to the operational or audit database. It consumes an
// export produced by the Python edge (see GET /api/v1/edge/outbox/snapshot) and
// returns verdicts for the gated recovery flow to apply.
//
// Usage:
//
//	vejit-sync --baseline baseline.json --input outbox.jsonl [--out decisions.jsonl]
//	vejit-sync --baseline baseline.json --input - < outbox.jsonl
//
// Input delta format:
//
//	{"event_id":"e1","event_type":"pos.transaction.completed","sku":"MILK","delta":-2,"kind":"SALE"}
//
// Verdict format:
//
//	{"event_id":"e1","sku":"MILK","action":"commutative|quarantine","reason":"..."}
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	"vendarix/sync-go/classify"
)

func main() {
	var (
		baselinePath = flag.String("baseline", "", "path to baseline JSON object: {\"SKU\": on_hand, ...}")
		inputPath    = flag.String("input", "-", "delta snapshot JSONL path, or '-' for stdin")
		outPath      = flag.String("out", "", "verdict output path (default stdout)")
	)
	flag.Parse()
	if *baselinePath == "" {
		fatal("--baseline is required")
	}

	baseline, err := readBaseline(*baselinePath)
	if err != nil {
		fatal(err.Error())
	}

	deltas, err := readDeltas(*inputPath)
	if err != nil {
		fatal(err.Error())
	}

	verdicts := classify.Classify(deltas, baseline)

	w := io.Writer(os.Stdout)
	if *outPath != "" {
		file, err := os.Create(*outPath)
		if err != nil {
			fatal(err.Error())
		}
		defer file.Close()
		w = file
	}

	enc := json.NewEncoder(w)
	for _, verdict := range verdicts {
		if err := enc.Encode(verdict); err != nil {
			fatal(err.Error())
		}
	}
}

func readBaseline(path string) (map[string]int, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read baseline: %w", err)
	}
	var baseline map[string]int
	if err := json.Unmarshal(data, &baseline); err != nil {
		return nil, fmt.Errorf("parse baseline: %w", err)
	}
	return baseline, nil
}

func readDeltas(path string) ([]classify.Delta, error) {
	var reader io.Reader = os.Stdin
	if path != "-" {
		file, err := os.Open(path)
		if err != nil {
			return nil, fmt.Errorf("open input: %w", err)
		}
		defer file.Close()
		reader = file
	}

	deltas := []classify.Delta{}
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	lineNo := 0
	for scanner.Scan() {
		lineNo++
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var delta classify.Delta
		if err := json.Unmarshal(line, &delta); err != nil {
			return nil, fmt.Errorf("line %d: parse delta: %w", lineNo, err)
		}
		deltas = append(deltas, delta)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read input: %w", err)
	}
	return deltas, nil
}

func fatal(message string) {
	fmt.Fprintln(os.Stderr, "vejit-sync: "+message)
	os.Exit(1)
}
