package classify

import (
	"reflect"
	"testing"
)

func TestAllSalesCommute(t *testing.T) {
	deltas := []Delta{
		{EventID: "e1", SKU: "MILK", Delta: -2, Kind: Sale},
		{EventID: "e2", SKU: "MILK", Delta: -3, Kind: Sale},
		{EventID: "e3", SKU: "BREAD", Delta: -1, Kind: Sale},
	}
	if !Commutative(deltas) {
		t.Fatal("expected all-sale batch to commute")
	}
	verdicts := Classify(deltas, map[string]int{"MILK": 10, "BREAD": 5})
	for _, v := range verdicts {
		if v.Action != "commutative" {
			t.Fatalf("expected commutative for %s, got %s (%s)", v.EventID, v.Action, v.Reason)
		}
	}
	for _, d := range Aggregate(deltas) {
		if d.Kind != Sale {
			t.Fatalf("aggregate delta must stay SALE, got %s", d.Kind)
		}
	}
	got := Aggregate(deltas)
	want := []Delta{
		{EventID: "agg_BREAD", SKU: "BREAD", Delta: -1, Kind: Sale},
		{EventID: "agg_MILK", SKU: "MILK", Delta: -5, Kind: Sale},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("aggregate mismatch: got %+v, want %+v", got, want)
	}
}

func TestRestockDoesNotCommuteWithPendingSales(t *testing.T) {
	deltas := []Delta{
		{EventID: "e1", SKU: "MILK", Delta: -2, Kind: Sale},
		{EventID: "r1", SKU: "MILK", Delta: 10, Kind: Restock},
	}
	verdicts := Classify(deltas, map[string]int{"MILK": 10})
	byID := map[string]Verdict{}
	for _, v := range verdicts {
		byID[v.EventID] = v
	}
	if byID["e1"].Action != "commutative" {
		t.Fatalf("sale still commutes: %+v", byID["e1"])
	}
	if byID["r1"].Action != "quarantine" {
		t.Fatalf("restock vs pending sale must quarantine: %+v", byID["r1"])
	}
}

func TestOversellQuarantines(t *testing.T) {
	deltas := []Delta{
		{EventID: "e1", SKU: "MILK", Delta: -12, Kind: Sale},
	}
	verdicts := Classify(deltas, map[string]int{"MILK": 10})
	if verdicts[0].Action != "quarantine" {
		t.Fatalf("oversell must quarantine, got %+v", verdicts[0])
	}
}

func TestMissingBaselineFailsClosed(t *testing.T) {
	deltas := []Delta{
		{EventID: "e1", SKU: "CHEESE", Delta: -1, Kind: Sale},
	}
	verdicts := Classify(deltas, map[string]int{})
	if verdicts[0].Action != "quarantine" {
		t.Fatalf("unknown baseline must fail closed, got %+v", verdicts[0])
	}
}

func TestUnknownKindFailsClosed(t *testing.T) {
	deltas := []Delta{
		{EventID: "e1", SKU: "MILK", Delta: -1, Kind: "PRICE_CHANGE"},
	}
	verdicts := Classify(deltas, map[string]int{"MILK": 10})
	if verdicts[0].Action != "quarantine" {
		t.Fatalf("unknown kind must fail closed, got %+v", verdicts[0])
	}
}

func TestTransferDoesNotCommuteWithSales(t *testing.T) {
	deltas := []Delta{
		{EventID: "e1", SKU: "MILK", Delta: -1, Kind: Sale},
		{EventID: "t1", SKU: "MILK", Delta: -4, Kind: Transfer},
	}
	verdicts := Classify(deltas, map[string]int{"MILK": 10})
	byID := map[string]Verdict{}
	for _, v := range verdicts {
		byID[v.EventID] = v
	}
	if byID["t1"].Action != "quarantine" {
		t.Fatalf("transfer must not commute with pending sales: %+v", byID["t1"])
	}
}

func TestEmptyBatch(t *testing.T) {
	if got := Classify(nil, map[string]int{}); len(got) != 0 {
		t.Fatalf("expected empty verdict set, got %+v", got)
	}
	if Commutative(nil) != true {
		t.Fatal("empty batch vacuously commutes")
	}
	if got := Aggregate(nil); len(got) != 0 {
		t.Fatalf("expected empty aggregate, got %+v", got)
	}
}
