// package classifier implements the commutative delta conflict classifier used
// by the watermark-replay step: when an edge node reconnects after a long
// offline period, its outbound events must be replayed without false-positive
// version conflicts against cloud state.
//
// The rule set (deterministic, total, no model in the loop):
//
//   - Pure SALE deltas (all negative) commute with each other. A bulk sales
//     replay can be applied as one aggregate commutative delta per SKU.
//   - RESTOCK/ADJUSTMENT deltas do NOT commute with SALEs on the same SKU â€”
//     ordering matters â€” so they are quarantined for human review.
//   - Any event that would drive on-hand below the supplied baseline, or an
//     oversell condition, is quarantined regardless of mix.
//   - Events with unknown action classes are quarantined (fail closed).
//
// The classifier never writes to the operational or audit database. It only
// emits verdicts for a human (or the gated recovery flow) to apply.
package classifier

import (
	"fmt"
	"sort"
)

// Kind is the direction/type of an inventory-affecting delta.
type Kind string

const (
	Sale      Kind = "SALE"
	Restock   Kind = "RESTOCK"
	Adjust    Kind = "ADJUSTMENT"
	Transfer  Kind = "TRANSFER"
	Waste     Kind = "WASTE"
	TransferIn Kind = "TRANSFER_IN"
)

// Delta is one outbound inventory movement to replay.
type Delta struct {
	EventID   string `json:"event_id"`
	EventType string `json:"event_type"`
	SKU       string `json:"sku"`
	Delta     int    `json:"delta"`
	Kind      Kind   `json:"kind"`
}

// Verdict is the classifier's decision for one delta.
type Verdict struct {
	EventID string `json:"event_id"`
	SKU     string `json:"sku"`
	Action  string `json:"action"` // "commutative" | "quarantine"
	Reason  string `json:"reason"`
}

// Commutative reports whether every delta in the batch commutes (all SALE).
func Commutative(deltas []Delta) bool {
	for _, d := range deltas {
		if d.Kind != Sale {
			return false
		}
	}
	return true
}

// Classify partitions deltas per SKU and returns one verdict per delta.
// baseline maps SKU -> on-hand at the last synced watermark; a missing SKU
// baseline means "unknown", which forces quarantine (fail closed).
func Classify(deltas []Delta, baseline map[string]int) []Verdict {
	bySku := map[string][]Delta{}
	for _, d := range deltas {
		bySku[d.SKU] = append(bySku[d.SKU], d)
	}

	skus := make([]string, 0, len(bySku))
	for sku := range bySku {
		skus = append(skus, sku)
	}
	sort.Strings(skus)

	verdicts := make([]Verdict, 0, len(deltas))
	for _, sku := range skus {
		skuDeltas := bySku[sku]
		onHand, known := baseline[sku]

		// Oversell check against baseline (independent of mix).
		sales := 0
		for _, d := range skuDeltas {
			if d.Delta < 0 {
				sales += -d.Delta
			}
		}

		for _, d := range skuDeltas {
			verdicts = append(verdicts, classifyOne(d, skuDeltas, known, onHand, sales))
		}
	}
	return verdicts
}

func classifyOne(d Delta, skuDeltas []Delta, baselineKnown bool, onHand, totalSales int) Verdict {
	base := Verdict{EventID: d.EventID, SKU: d.SKU}

	if d.Kind != Sale && d.Kind != Restock && d.Kind != Adjust && d.Kind != Transfer && d.Kind != TransferIn && d.Kind != Waste {
		base.Action, base.Reason = "quarantine", fmt.Sprintf("unknown delta kind %q (fail closed)", d.Kind)
		return base
	}
	if d.SKU == "" {
		base.Action, base.Reason = "quarantine", "missing sku"
		return base
	}
	if !baselineKnown {
		base.Action, base.Reason = "quarantine", "no baseline for sku at watermark (fail closed)"
		return base
	}
	if onHand-totalSales < 0 {
		base.Action, base.Reason = "quarantine", fmt.Sprintf("replay would oversell %s: on-hand %d, pending sales %d", d.SKU, onHand, totalSales)
		return base
	}
	if d.Kind == Sale {
		base.Action, base.Reason = "commutative", "sale deltas commute; apply as one aggregate delta per sku"
		return base
	}
	// Non-sale (RESTOCK / ADJUSTMENT / TRANSFER) does not commute with pending SALEs.
	for _, other := range skuDeltas {
		if other.Kind == Sale {
			base.Action, base.Reason = "quarantine", fmt.Sprintf("%s does not commute with pending SALEs for %s", d.Kind, d.SKU)
			return base
		}
	}
	base.Action, base.Reason = "quarantine", fmt.Sprintf("%s requires human review (non-commutative against unknown cloud state)", d.Kind)
	return base
}

// Aggregate compresses a purely-commutative batch (all SALE) into one delta
// per SKU. Caller must only invoke this when Commutative(deltas) is true.
func Aggregate(deltas []Delta) []Delta {
	bySku := map[string]int{}
	for _, d := range deltas {
		bySku[d.SKU] += d.Delta
	}
	out := make([]Delta, 0, len(bySku))
	skus := make([]string, 0, len(bySku))
	for sku := range bySku {
		skus = append(skus, sku)
	}
	sort.Strings(skus)
	for _, sku := range skus {
		out = append(out, Delta{
			EventID: fmt.Sprintf("agg_%s", sku),
			SKU:     sku,
			Delta:   bySku[sku],
			Kind:    Sale,
		})
	}
	return out
}
