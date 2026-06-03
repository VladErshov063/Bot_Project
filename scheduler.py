from datetime import date, timedelta

def calculate_next_review(success: bool, difficulty: int, forgetting_curve: float, review_count: int) -> tuple[date, int]:
    if success:
        new_count = review_count + 1
        if new_count == 1:
            interval = 1
        elif new_count == 2:
            interval = 3
        elif new_count == 3:
            interval = 7
        else:
            interval = 14
        interval = int(interval * forgetting_curve * (5 / difficulty))
        interval = max(1, min(interval, 90))
        next_date = date.today() + timedelta(days=interval)
        return next_date, new_count
    else:
        next_date = date.today() + timedelta(days=1)
        return next_date, review_count
