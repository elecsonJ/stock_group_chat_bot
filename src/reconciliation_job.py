from reconciliation import PaperStateReconciler


def main():
    reconciler = PaperStateReconciler()
    report = reconciler.run(persist=True)
    print(reconciler.render(report))
    if report.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
