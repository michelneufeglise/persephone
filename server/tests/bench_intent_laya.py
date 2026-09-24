"""
Benchmark for Laya decide_intent and decide_file_role functions.

Runs ~56 intent samples (8 per category) and ~16 role samples (8 subject, 8 reference)
through the REAL Laya model and reports accuracy, latency, confusion, and gating metrics.

Run with: python3 -m pytest server/tests/bench_intent_laya.py -s
Or directly: cd /path/to/repo && HF_HUB_OFFLINE=1 python3 server/tests/bench_intent_laya.py
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import json
import time
import statistics
from collections import defaultdict
import laya_decider as _laya


# Sample benchmark data: 56 intent samples (8 per intent) and 16 role samples
BENCHMARK_DATA = {
    "intent_samples": [
        # verify_signature samples
        {"intent": "verify_signature", "message": "verify the signature on this contract against the reference card", "files": [{"name": "contract.pdf", "kind": "pdf"}, {"name": "signature_ref.png", "kind": "image"}]},
        {"intent": "verify_signature", "message": "is this handwriting authentic? compare to the original", "files": [{"name": "original.pdf", "kind": "pdf"}, {"name": "sample.png", "kind": "image"}]},
        {"intent": "verify_signature", "message": "check if signatures match", "files": [{"name": "sig1.jpg", "kind": "image"}, {"name": "sig2.jpg", "kind": "image"}]},
        {"intent": "verify_signature", "message": "validate handwriting authenticity against specimen card", "files": [{"name": "doc.pdf", "kind": "pdf"}, {"name": "specimen.jpg", "kind": "image"}]},
        {"intent": "verify_signature", "message": "si ces signatures correspondent", "files": [{"name": "cheque.pdf", "kind": "pdf"}, {"name": "ref.png", "kind": "image"}]},  # French
        {"intent": "verify_signature", "message": "compare the two signatures - are they the same person?", "files": [{"name": "doc.pdf", "kind": "pdf"}, {"name": "card.png", "kind": "image"}]},
        {"intent": "verify_signature", "message": "verify autograph authenticity", "files": [{"name": "letter.pdf", "kind": "pdf"}]},
        {"intent": "verify_signature", "message": "does this signature match?", "files": [{"name": "form.pdf", "kind": "pdf"}]},

        # identify_person samples
        {"intent": "identify_person", "message": "who is this document about?", "files": [{"name": "letter.pdf", "kind": "pdf"}]},
        {"intent": "identify_person", "message": "find the person's name", "files": [{"name": "passport.pdf", "kind": "pdf"}]},
        {"intent": "identify_person", "message": "whose document is this?", "files": [{"name": "contract.docx", "kind": "docx"}]},
        {"intent": "identify_person", "message": "identify the individual mentioned in this", "files": [{"name": "email.eml", "kind": "email"}]},
        {"intent": "identify_person", "message": "wat is de naam van de ondertekender", "files": [{"name": "doc.pdf", "kind": "pdf"}]},  # Dutch
        {"intent": "identify_person", "message": "extract the person's full name and date of birth", "files": [{"name": "id_card.png", "kind": "image"}]},
        {"intent": "identify_person", "message": "who owns this", "files": [{"name": "certificate.pdf", "kind": "pdf"}]},
        {"intent": "identify_person", "message": "find author", "files": [{"name": "memo.docx", "kind": "docx"}]},

        # summarize samples
        {"intent": "summarize", "message": "summarize the key points", "files": [{"name": "report.pdf", "kind": "pdf"}]},
        {"intent": "summarize", "message": "tl;dr this document", "files": [{"name": "contract.pdf", "kind": "pdf"}]},
        {"intent": "summarize", "message": "what are the main takeaways?", "files": [{"name": "article.docx", "kind": "docx"}]},
        {"intent": "summarize", "message": "give me an overview", "files": [{"name": "proposal.pdf", "kind": "pdf"}]},
        {"intent": "summarize", "message": "resume les points importants", "files": [{"name": "doc.pdf", "kind": "pdf"}]},  # French
        {"intent": "summarize", "message": "3 sentence summary of this", "files": [{"name": "email.eml", "kind": "email"}]},
        {"intent": "summarize", "message": "summarize", "files": [{"name": "long_document.pdf", "kind": "pdf"}]},
        {"intent": "summarize", "message": "abstract this content", "files": [{"name": "paper.pdf", "kind": "pdf"}]},

        # extract_data samples
        {"intent": "extract_data", "message": "extract all invoice amounts and dates", "files": [{"name": "invoice.pdf", "kind": "pdf"}]},
        {"intent": "extract_data", "message": "pull all table data from this spreadsheet", "files": [{"name": "data.xlsx", "kind": "xlsx"}]},
        {"intent": "extract_data", "message": "get the form fields: name, email, phone", "files": [{"name": "form.pdf", "kind": "pdf"}]},
        {"intent": "extract_data", "message": "extract entity names and dates", "files": [{"name": "document.pdf", "kind": "pdf"}]},
        {"intent": "extract_data", "message": "extrait les nombres et montants", "files": [{"name": "facture.pdf", "kind": "pdf"}]},  # French
        {"intent": "extract_data", "message": "pull billing info and amounts", "files": [{"name": "statement.pdf", "kind": "pdf"}]},
        {"intent": "extract_data", "message": "get the tables", "files": [{"name": "sheet.xlsx", "kind": "xlsx"}]},
        {"intent": "extract_data", "message": "extract structured metadata", "files": [{"name": "metadata.docx", "kind": "docx"}]},

        # translate samples
        {"intent": "translate", "message": "translate this to English", "files": [{"name": "french_doc.pdf", "kind": "pdf"}]},
        {"intent": "translate", "message": "what does this Spanish text say?", "files": [{"name": "spanish.pdf", "kind": "pdf"}]},
        {"intent": "translate", "message": "translate to German", "files": [{"name": "document.pdf", "kind": "pdf"}]},
        {"intent": "translate", "message": "is this translation accurate?", "files": [{"name": "original.pdf", "kind": "pdf"}, {"name": "translated.pdf", "kind": "pdf"}]},
        {"intent": "translate", "message": "vertaal naar Nederlands", "files": [{"name": "doc.pdf", "kind": "pdf"}]},  # Dutch
        {"intent": "translate", "message": "translate the content to French", "files": [{"name": "english.docx", "kind": "docx"}]},
        {"intent": "translate", "message": "translation check", "files": [{"name": "text.pdf", "kind": "pdf"}]},
        {"intent": "translate", "message": "what does it say in English?", "files": [{"name": "foreign.pdf", "kind": "pdf"}]},

        # redact samples
        {"intent": "redact", "message": "remove all personal information", "files": [{"name": "document.pdf", "kind": "pdf"}]},
        {"intent": "redact", "message": "hide the SSN numbers", "files": [{"name": "form.pdf", "kind": "pdf"}]},
        {"intent": "redact", "message": "black out sensitive data", "files": [{"name": "report.pdf", "kind": "pdf"}]},
        {"intent": "redact", "message": "redact confidential sections", "files": [{"name": "contract.docx", "kind": "docx"}]},
        {"intent": "redact", "message": "masquer les informations privées", "files": [{"name": "doc.pdf", "kind": "pdf"}]},  # French
        {"intent": "redact", "message": "remove addresses and phone numbers", "files": [{"name": "list.pdf", "kind": "pdf"}]},
        {"intent": "redact", "message": "obscure personal details", "files": [{"name": "email.eml", "kind": "email"}]},
        {"intent": "redact", "message": "scrub confidential", "files": [{"name": "memo.docx", "kind": "docx"}]},

        # general_question samples
        {"intent": "general_question", "message": "what can you tell me about this?", "files": [{"name": "document.pdf", "kind": "pdf"}]},
        {"intent": "general_question", "message": "analyze this", "files": [{"name": "data.xlsx", "kind": "xlsx"}]},
        {"intent": "general_question", "message": "help me understand this document", "files": [{"name": "report.pdf", "kind": "pdf"}]},
        {"intent": "general_question", "message": "what's in here?", "files": [{"name": "file.pdf", "kind": "pdf"}]},
        {"intent": "general_question", "message": "wat denk je hiervan", "files": [{"name": "doc.pdf", "kind": "pdf"}]},  # Dutch
        {"intent": "general_question", "message": "read this and tell me what you think", "files": [{"name": "letter.pdf", "kind": "pdf"}]},
        {"intent": "general_question", "message": "check this out", "files": [{"name": "document.docx", "kind": "docx"}]},
        {"intent": "general_question", "message": "review document plz", "files": [{"name": "doc.txt", "kind": "text"}]},
    ],
    "role_samples": [
        # subject_document samples
        {"role": "subject_document", "message": "analyze this contract", "file": {"name": "contract.pdf", "kind": "pdf", "snippet": "Agreement between parties A and B for services"}},
        {"role": "subject_document", "message": "extract data from this form", "file": {"name": "form.pdf", "kind": "pdf", "snippet": "Name: _____ Email: _____ Phone: _____"}},
        {"role": "subject_document", "message": "summarize the report", "file": {"name": "report.pdf", "kind": "pdf", "snippet": "Q3 2024 Financial Report"}},
        {"role": "subject_document", "message": "examine this invoice", "file": {"name": "invoice.pdf", "kind": "pdf", "snippet": "Invoice #2024-001 Amount Due: $5,000"}},
        {"role": "subject_document", "message": "what's in this document?", "file": {"name": "letter.pdf", "kind": "pdf", "snippet": "Dear Sir or Madam,"}},
        {"role": "subject_document", "message": "process this email", "file": {"name": "message.eml", "kind": "email", "snippet": "Subject: Important update - Please review the attached proposal"}},
        {"role": "subject_document", "message": "translate this", "file": {"name": "foreign.pdf", "kind": "pdf", "snippet": "Bonjour, voici le document"}},
        {"role": "subject_document", "message": "redact sensitive info from this", "file": {"name": "memo.docx", "kind": "docx", "snippet": "Confidential memo to all staff members"}},

        # reference_specimen samples
        {"role": "reference_specimen", "message": "verify the signature against this reference card", "file": {"name": "signature_card.png", "kind": "image", "snippet": "Sample Signatures: J. Smith"}},
        {"role": "reference_specimen", "message": "compare against the original", "file": {"name": "original_document.pdf", "kind": "pdf", "snippet": "Original version signed by"}},
        {"role": "reference_specimen", "message": "check this reference specimen", "file": {"name": "specimen.jpg", "kind": "image", "snippet": "Handwriting sample from certified source"}},
        {"role": "reference_specimen", "message": "use this as comparison", "file": {"name": "template.pdf", "kind": "pdf", "snippet": "Template format for comparison"}},
        {"role": "reference_specimen", "message": "match against this sample", "file": {"name": "sample_card.png", "kind": "image", "snippet": "Sample ID card with official signature"}},
        {"role": "reference_specimen", "message": "verify against this ID", "file": {"name": "id.pdf", "kind": "pdf", "snippet": "Government-issued ID card"}},
        {"role": "reference_specimen", "message": "this is the authentic version", "file": {"name": "certified_copy.pdf", "kind": "pdf", "snippet": "Certified true copy of original"}},
        {"role": "reference_specimen", "message": "use for authenticity check", "file": {"name": "auth_cert.png", "kind": "image", "snippet": "Certificate of authenticity"}},
    ]
}


def benchmark_intents():
    """Run benchmark on intent classification."""
    print("\n" + "="*70)
    print("INTENT CLASSIFICATION BENCHMARK")
    print("="*70)

    samples = BENCHMARK_DATA["intent_samples"]
    correct = 0
    total = len(samples)
    latencies = []
    confidences_correct = []
    confidences_wrong = []
    per_intent_correct = defaultdict(int)
    per_intent_total = defaultdict(int)
    confusion_matrix = defaultdict(lambda: defaultdict(int))

    for sample in samples:
        expected_intent = sample["intent"]
        message = sample["message"]
        files = sample["files"]

        start = time.time()
        result = _laya.decide_intent(message, files)
        latency = (time.time() - start) * 1000  # ms

        if result is None:
            print(f"  SKIP (router unavailable): {message[:50]}...")
            continue

        latencies.append(latency)
        predicted_intent = result["intent"]
        confidence = result["confidence"]

        per_intent_total[expected_intent] += 1
        confusion_matrix[expected_intent][predicted_intent] += 1

        is_correct = predicted_intent == expected_intent
        if is_correct:
            correct += 1
            per_intent_correct[expected_intent] += 1
            confidences_correct.append(confidence)
        else:
            confidences_wrong.append(confidence)

        status = "✓" if is_correct else "✗"
        print(f"  {status} Expected: {expected_intent:20s} Predicted: {predicted_intent:20s} (conf={confidence:.2f}, {latency:.1f}ms)")

    # Report aggregate metrics
    accuracy = correct / total if total > 0 else 0
    print("\n" + "-"*70)
    print(f"OVERALL ACCURACY: {correct}/{total} = {accuracy:.1%}")

    # Per-intent accuracy
    print("\nPER-INTENT ACCURACY:")
    for intent in _laya.INTENTS.keys():
        correct_count = per_intent_correct[intent]
        total_count = per_intent_total[intent]
        acc = correct_count / total_count if total_count > 0 else 0
        print(f"  {intent:20s}: {correct_count}/{total_count} = {acc:.1%}")

    # Confusion matrix
    print("\nCONFUSION MATRIX (expected → predicted):")
    for expected in sorted(confusion_matrix.keys()):
        for predicted in sorted(confusion_matrix[expected].keys()):
            count = confusion_matrix[expected][predicted]
            if count > 0:
                print(f"  {expected:20s} → {predicted:20s}: {count:3d}")

    # Confidence statistics
    if confidences_correct:
        mean_conf_correct = statistics.mean(confidences_correct)
    else:
        mean_conf_correct = 0
    if confidences_wrong:
        mean_conf_wrong = statistics.mean(confidences_wrong)
    else:
        mean_conf_wrong = 0

    print(f"\nCONFIDENCE STATISTICS:")
    print(f"  Mean confidence (correct): {mean_conf_correct:.3f}")
    print(f"  Mean confidence (wrong):   {mean_conf_wrong:.3f}")

    # Latency statistics
    if latencies:
        median_latency = statistics.median(latencies)
        mean_latency = statistics.mean(latencies)
        print(f"\nLATENCY STATISTICS:")
        print(f"  Median: {median_latency:.1f}ms")
        print(f"  Mean:   {mean_latency:.1f}ms")

    # Gating analysis for different confidence thresholds
    print(f"\nGATING ANALYSIS (LAYA_INTENT_MIN_CONFIDENCE = {_laya.LAYA_INTENT_MIN_CONFIDENCE}):")
    for threshold in [0.5, 0.6, 0.7, 0.8, 0.9]:
        covered = sum(1 for s in BENCHMARK_DATA["intent_samples"]
                      if _laya.decide_intent(s["message"], s["files"]) is None or
                      _laya.decide_intent(s["message"], s["files"]).get("confidence", 0) >= threshold)
        coverage = covered / total if total > 0 else 0

        # Accuracy on covered subset (simplified: just count correct predictions above threshold)
        covered_correct = 0
        covered_total = 0
        for i, sample in enumerate(BENCHMARK_DATA["intent_samples"]):
            result = _laya.decide_intent(sample["message"], sample["files"])
            if result and result["confidence"] >= threshold:
                covered_total += 1
                if result["intent"] == sample["intent"]:
                    covered_correct += 1

        covered_acc = covered_correct / covered_total if covered_total > 0 else 0
        print(f"  Threshold {threshold:.1f}: coverage={covered}/{total} ({coverage:.1%}), accuracy on covered={covered_correct}/{covered_total} ({covered_acc:.1%})")


def benchmark_roles():
    """Run benchmark on file role classification."""
    print("\n" + "="*70)
    print("FILE ROLE CLASSIFICATION BENCHMARK")
    print("="*70)

    samples = BENCHMARK_DATA["role_samples"]
    correct = 0
    total = len(samples)
    latencies = []
    confidences_correct = []
    confidences_wrong = []
    per_role_correct = defaultdict(int)
    per_role_total = defaultdict(int)
    confusion_matrix = defaultdict(lambda: defaultdict(int))

    for sample in samples:
        expected_role = sample["role"]
        message = sample["message"]
        file = sample["file"]

        start = time.time()
        result = _laya.decide_file_role(message, file)
        latency = (time.time() - start) * 1000  # ms

        if result is None:
            print(f"  SKIP (router unavailable): {message[:50]}...")
            continue

        latencies.append(latency)
        predicted_role = result["role"]
        confidence = result["confidence"]

        per_role_total[expected_role] += 1
        confusion_matrix[expected_role][predicted_role] += 1

        is_correct = predicted_role == expected_role
        if is_correct:
            correct += 1
            per_role_correct[expected_role] += 1
            confidences_correct.append(confidence)
        else:
            confidences_wrong.append(confidence)

        status = "✓" if is_correct else "✗"
        print(f"  {status} Expected: {expected_role:20s} Predicted: {predicted_role:20s} (conf={confidence:.2f}, {latency:.1f}ms)")

    # Report aggregate metrics
    accuracy = correct / total if total > 0 else 0
    print("\n" + "-"*70)
    print(f"OVERALL ACCURACY: {correct}/{total} = {accuracy:.1%}")

    # Per-role accuracy
    print("\nPER-ROLE ACCURACY:")
    for role in ["subject_document", "reference_specimen"]:
        correct_count = per_role_correct[role]
        total_count = per_role_total[role]
        acc = correct_count / total_count if total_count > 0 else 0
        print(f"  {role:20s}: {correct_count}/{total_count} = {acc:.1%}")

    # Confusion matrix
    print("\nCONFUSION MATRIX (expected → predicted):")
    for expected in sorted(confusion_matrix.keys()):
        for predicted in sorted(confusion_matrix[expected].keys()):
            count = confusion_matrix[expected][predicted]
            if count > 0:
                print(f"  {expected:20s} → {predicted:20s}: {count:3d}")

    # Confidence statistics
    if confidences_correct:
        mean_conf_correct = statistics.mean(confidences_correct)
    else:
        mean_conf_correct = 0
    if confidences_wrong:
        mean_conf_wrong = statistics.mean(confidences_wrong)
    else:
        mean_conf_wrong = 0

    print(f"\nCONFIDENCE STATISTICS:")
    print(f"  Mean confidence (correct): {mean_conf_correct:.3f}")
    print(f"  Mean confidence (wrong):   {mean_conf_wrong:.3f}")

    # Latency statistics
    if latencies:
        median_latency = statistics.median(latencies)
        mean_latency = statistics.mean(latencies)
        print(f"\nLATENCY STATISTICS:")
        print(f"  Median: {median_latency:.1f}ms")
        print(f"  Mean:   {mean_latency:.1f}ms")

    # Gating analysis for different confidence thresholds
    print(f"\nGATING ANALYSIS (LAYA_ROLE_MIN_CONFIDENCE = {_laya.LAYA_ROLE_MIN_CONFIDENCE}):")
    for threshold in [0.5, 0.6, 0.7, 0.8, 0.9]:
        # Accuracy on covered subset
        covered_correct = 0
        covered_total = 0
        for sample in BENCHMARK_DATA["role_samples"]:
            result = _laya.decide_file_role(sample["message"], sample["file"])
            if result and result["confidence"] >= threshold:
                covered_total += 1
                if result["role"] == sample["role"]:
                    covered_correct += 1

        coverage = covered_total / total if total > 0 else 0
        covered_acc = covered_correct / covered_total if covered_total > 0 else 0
        print(f"  Threshold {threshold:.1f}: coverage={covered_total}/{total} ({coverage:.1%}), accuracy on covered={covered_correct}/{covered_total} ({covered_acc:.1%})")


if __name__ == "__main__":
    # Check that Laya is available before running
    if not _laya.is_available():
        print("ERROR: Laya model not available. Run:")
        print("  python3 -m laya_decider ensure_downloaded")
        print("Or set HF_HUB_OFFLINE=1 if model is already cached.")
        sys.exit(1)

    benchmark_intents()
    benchmark_roles()

    print("\n" + "="*70)
    print("BENCHMARK COMPLETE")
    print("="*70)
