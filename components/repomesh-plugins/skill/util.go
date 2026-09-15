package skill

import (
	"bytes"
	"encoding/json"
)

func mustJSON(v map[string]any) string {
	if v == nil {
		return "{}"
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return "{}"
	}
	return buf.String()
}

func parseJSON(text string) map[string]any {
	out := map[string]any{}
	if text == "" {
		return out
	}
	_ = json.Unmarshal([]byte(text), &out)
	return out
}

// TokenSimilarity scores how much two texts share, used for the revision
// auto-release check (改动 > 30% 强制重审). Change ratio = 1 - shared tokens /
// longer text's token count, so swapping 1 word in 4 yields 0.75 (auto-pass),
// while a full rewrite yields ~0 (forced re-review).
func TokenSimilarity(a, b string) float64 {
	setA := tokenSet(a)
	setB := tokenSet(b)
	if len(setA) == 0 && len(setB) == 0 {
		return 1
	}
	shared := 0
	for t := range setA {
		if setB[t] {
			shared++
		}
	}
	longer := len(setA)
	if len(setB) > longer {
		longer = len(setB)
	}
	if longer == 0 {
		return 1
	}
	return float64(shared) / float64(longer)
}

func tokenSet(s string) map[string]bool {
	out := map[string]bool{}
	start := -1
	for i := 0; i <= len(s); i++ {
		isSep := i == len(s) || s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r'
		if isSep {
			if start >= 0 && i-start > 0 {
				out[s[start:i]] = true
			}
			start = -1
		} else if start < 0 {
			start = i
		}
	}
	return out
}
