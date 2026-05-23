import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from performance_tracker import PerformanceTracker
from replay_engine import ReplayEngine


def main():
    event_id = os.getenv("REPLAY_EVENT_ID", "").strip()
    limit = int(os.getenv("REPLAY_LIMIT", "50"))
    split_date = os.getenv("REPLAY_SPLIT_DATE", "").strip()
    horizon = os.getenv("REPLAY_HORIZON", "1d").strip() or "1d"
    run_name = os.getenv("REPLAY_RUN_NAME", "manual_replay").strip() or "manual_replay"
    engine = ReplayEngine()
    tracker = PerformanceTracker(engine.db)

    if event_id:
        rows = engine.replay_event(event_id)
        summary = tracker.summarize(event_id=event_id, horizon=horizon)
        curve = tracker.build_equity_curve(rows=rows, horizon=horizon)
        print("=== Replay Result ===")
        print(f"- event_id: {event_id}")
        print(f"- measurements: {len(rows)}")
        print(f"- win_rate: {summary['win_rate']:.2%}")
        print(f"- avg_return_pct: {summary['avg_return_pct']:.2f}")
        print(f"- avg_alpha_pct: {summary['avg_alpha_pct']:.2f}")
        print(f"- expectancy_pct: {summary['expectancy_pct']:.2f}")
        print(f"- max_drawdown_pct: {curve['max_drawdown_pct']:.2f}")
        print(f"- total_return_pct: {curve['total_return_pct']:.2f}")
        feedback = tracker.build_feedback_report(horizon=horizon)
        tracker.save_feedback_profile(feedback)
        print("\n" + tracker.render_feedback_report(feedback))
        return

    if split_date:
        result = engine.replay_split(
            split_date=split_date,
            run_name=run_name,
            horizon=horizon,
            limit=limit,
        )
        train = result["train_summary"]
        test = result["test_summary"]
        print("=== Replay Split Result ===")
        print(f"- run_name: {run_name}")
        print(f"- split_date: {split_date}")
        print(f"- train_count: {train['measurement_count']} | train_avg_alpha: {train['avg_alpha_pct']:.2f} | train_mdd: {train['max_drawdown_pct']:.2f}")
        print(f"- test_count: {test['measurement_count']} | test_avg_alpha: {test['avg_alpha_pct']:.2f} | test_mdd: {test['max_drawdown_pct']:.2f}")
        feedback = tracker.build_feedback_report(horizon=horizon)
        tracker.save_feedback_profile(feedback)
        print("\n" + tracker.render_feedback_report(feedback))
        return

    rows = engine.replay_recent(limit=limit)
    summary = tracker.summarize(limit=max(100, limit * 4), horizon=horizon)
    curve = tracker.build_equity_curve(rows=rows, horizon=horizon)
    print("=== Replay Batch Result ===")
    print(f"- recent_events: {limit}")
    print(f"- measurements: {len(rows)}")
    print(f"- win_rate: {summary['win_rate']:.2%}")
    print(f"- avg_return_pct: {summary['avg_return_pct']:.2f}")
    print(f"- avg_alpha_pct: {summary['avg_alpha_pct']:.2f}")
    print(f"- expectancy_pct: {summary['expectancy_pct']:.2f}")
    print(f"- profit_factor: {summary['profit_factor']}")
    print(f"- max_drawdown_pct: {curve['max_drawdown_pct']:.2f}")
    print(f"- total_return_pct: {curve['total_return_pct']:.2f}")
    feedback = tracker.build_feedback_report(horizon=horizon)
    tracker.save_feedback_profile(feedback)
    print("\n" + tracker.render_feedback_report(feedback))


if __name__ == "__main__":
    main()
