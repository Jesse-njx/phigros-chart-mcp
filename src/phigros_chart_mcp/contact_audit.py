"""Audit declared contact ownership, without inferring fingers from raw objects."""
from collections import Counter, defaultdict
from fractions import Fraction
import heapq
import math


def audit_contacts(model, max_witnesses=20, rapid_repeat_ms=100.0, target_position=None):
    """Read an engine model; return bounded, explicitly hypothetical findings.

    target_position, when supplied, is (line, note, exact_time) -> (x, y).
    Its coordinate units must be documented by the caller. Without it, travel
    is reported only as local x change between notes on the same engine line.
    Holds occupy [head, tail); drags sharing a finger and explicit gesture ID
    are conservatively continuous from first through last target. No recovery
    buffer or maximum allowed finger count is silently imposed.
    """
    if isinstance(max_witnesses, bool) or not isinstance(max_witnesses, int) or not 1 <= max_witnesses <= 100:
        raise ValueError('max_witnesses must be an integer from 1 to 100')
    if isinstance(rapid_repeat_ms, bool) or not math.isfinite(rapid_repeat_ms) or not 0 <= rapid_repeat_ms <= 1000:
        raise ValueError('rapid_repeat_ms must be finite from 0 to 1000')
    q = lambda value: Fraction(str(value))
    heads = defaultdict(list)
    starts = defaultdict(list)
    drag_groups = defaultdict(list)
    assigned = []
    unassigned = Counter()
    offset = q(model.get('offset_ms', 0)) / 1000
    counts = Counter()
    findings = []

    def witness(code, time, finger, first, second=None, **extra):
        counts[code] += 1
        if len(findings) < max_witnesses:
            row = dict(code=code, chart_seconds=float(time), audio_seconds=float(time + offset),
                       finger=finger, note_id=first['note']['id'], line_id=first['line']['id'], **extra)
            if second is not None:
                row['other_note_id'] = second['note']['id']
                row['other_line_id'] = second['line']['id']
            findings.append(row)

    for line in model['lines']:
        for note in line['notes']:
            annotation = note.get('annotation', {})
            finger = annotation.get('finger_key')
            if not finger and annotation.get('hand') and annotation.get('finger'):
                finger = f"{annotation['hand']}_{annotation['finger']}"
            if not isinstance(finger, str) or not finger.strip():
                unassigned[note['kind']] += 1
                continue
            gesture = annotation.get('gesture')
            # Prefix prevents an arbitrary annotation colliding with an object ID.
            group = 'gesture:' + gesture if isinstance(gesture, str) and gesture else 'object:' + note['id']
            row = dict(line=line, note=note, finger=finger, group=group, time=q(note['time']))
            assigned.append(row)
            heads[row['time']].append(row)
            if note['kind'] == 'hold':
                starts[row['time']].append((q(note['end']), row))
            elif note['kind'] == 'drag' and isinstance(gesture, str) and gesture:
                drag_groups[(finger, group)].append(row)

    for rows in drag_groups.values():
        rows.sort(key=lambda r: r['time'])
        if rows[-1]['time'] > rows[0]['time']:
            starts[rows[0]['time']].append((rows[-1]['time'], rows[0]))

    # Active intervals are grouped by declared finger/gesture. Only one
    # representative collision is emitted per new head, avoiding quadratic
    # expansion when a malformed chart assigns many objects to one finger.
    active = defaultdict(dict)
    endings = []
    serial = 0
    peak = 0
    peak_witnesses = []
    latest_strike = {}
    fastest = []

    def other_group(groups, group):
        for key, members in groups.items():
            if key != group:
                return next(iter(members.values()))
        return None

    for at in sorted(heads):
        while endings and endings[0][0] <= at:
            _, ident, finger, group = heapq.heappop(endings)
            members = active[finger][group]
            del members[ident]
            if not members:
                del active[finger][group]
            if not active[finger]:
                del active[finger]
        simultaneous = defaultdict(dict)
        for row in heads[at]:
            finger, group = row['finger'], row['group']
            other = other_group(active.get(finger, {}), group)
            if other is None:
                other = other_group(simultaneous.get(finger, {}), group)
            if other is not None:
                witness('assigned_finger_collision', at, finger, row, other,
                        interpretation='Different declared gestures overlap; review shared targets or reassign ownership')
            simultaneous[finger].setdefault(group, {})[row['note']['id']] = row
            if row['note']['kind'] in ('tap', 'flick', 'hold'):
                previous = latest_strike.get(finger)
                if previous is not None and at > previous['time']:
                    gap = float((at - previous['time']) * 1000)
                    item = dict(finger=finger, from_note_id=previous['note']['id'], to_note_id=row['note']['id'],
                                from_chart_seconds=float(previous['time']), to_chart_seconds=float(at), gap_ms=gap)
                    if target_position is not None:
                        first = target_position(previous['line'], previous['note'], previous['time'])
                        second = target_position(row['line'], row['note'], at)
                        item['target_travel'] = math.hypot(second[0] - first[0], second[1] - first[1])
                    elif previous['line']['id'] == row['line']['id']:
                        item['local_x_delta'] = row['note']['x'] - previous['note']['x']
                    fastest.append(item)
                    fastest.sort(key=lambda r: r['gap_ms'])
                    del fastest[max_witnesses:]
                    if gap < rapid_repeat_ms:
                        witness('rapid_assigned_repeat', at, finger, row, previous, gap_ms=gap,
                                interpretation='Review speed and recovery; this threshold is a review heuristic, not a playability limit')
                latest_strike[finger] = row
        occupied = set(active) | set(simultaneous)
        if len(occupied) > peak:
            peak = len(occupied)
            peak_witnesses = []
        if len(occupied) == peak and len(peak_witnesses) < max_witnesses:
            peak_witnesses.append(dict(chart_seconds=float(at), audio_seconds=float(at + offset),
                                       fingers=sorted(occupied)))
        for end, row in starts[at]:
            serial += 1
            active[row['finger']].setdefault(row['group'], {})[serial] = row
            heapq.heappush(endings, (end, serial, row['finger'], row['group']))

    return dict(assigned_heads=len(assigned), unassigned_heads=sum(unassigned.values()),
                unassigned_by_kind=dict(unassigned), peak_declared_contacts=peak,
                peak_witnesses=peak_witnesses, fastest_same_finger_strikes=fastest,
                findings=findings, finding_counts=dict(counts), total_findings=sum(counts.values()),
                findings_truncated=sum(counts.values()) > len(findings), rapid_repeat_threshold_ms=rapid_repeat_ms,
                limitations=['Ownership annotations are hypotheses, not inferred independent fingers',
                             'Same gesture ID permits assumed sharing; target and trajectory compatibility still need review',
                             'Unlabelled drags have no inferred continuous occupancy',
                             'No reaction, hand anatomy, occlusion, or human playtest simulation'])
