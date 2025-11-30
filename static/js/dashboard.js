document.addEventListener("DOMContentLoaded", () => {
  // -------------------------------
  // Spending by category (donut)
  // -------------------------------
  const catCanvas = document.getElementById("categoryChart");

  if (catCanvas && window.Chart) {
    const rawCat = catCanvas.dataset.categoryJson || "[]";
    let payload;

    try {
      payload = JSON.parse(rawCat);
    } catch (e) {
      console.error("Bad category JSON", e);
      payload = [];
    }

    let labels = [];
    let data = [];

    if (Array.isArray(payload)) {
      // Format: [{category, amount}] OR [{label, value}]
      labels = payload.map((c) => c.category || c.label || "Uncategorized");
      data = payload.map((c) =>
        Math.abs(
          Number(
            c.amount !== undefined ? c.amount :
            c.value  !== undefined ? c.value  :
            0
          ) || 0
        )
      );
    } else if (payload && typeof payload === "object") {
      // Format: {labels: [...], data: [...]} or {labels: [...], values: [...]}
      labels = Array.isArray(payload.labels) ? payload.labels : [];
      const rawVals = Array.isArray(payload.data)
        ? payload.data
        : Array.isArray(payload.values)
        ? payload.values
        : [];

      data = rawVals.map((v) => Math.abs(Number(v) || 0));
    }

    new Chart(catCanvas.getContext("2d"), {
      type: "doughnut",
      data: {
        labels,
        datasets: [
          {
            data,
          },
        ],
      },
      options: {
        plugins: {
          legend: {
            position: "bottom",
          },
        },
      },
    });
  }

  // -------------------------------
  // Daily net line chart
  // -------------------------------
  const dailyCanvas = document.getElementById("dailyNetChart");

  if (dailyCanvas && window.Chart) {
    const rawDaily = dailyCanvas.dataset.dailyJson || "[]";
    let days;

    try {
      days = JSON.parse(rawDaily);
    } catch (e) {
      console.error("Bad daily JSON", e);
      days = [];
    }

    const labels = days.map((d) => d.date);
    const data = days.map((d) =>
      Number(
        d.net !== undefined ? d.net :
        d.amount !== undefined ? d.amount :
        0
      ) || 0
    );

    new Chart(dailyCanvas.getContext("2d"), {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Net",
            data,
            tension: 0.25,
          },
        ],
      },
      options: {
        scales: {
          x: { display: true },
          y: { display: true },
        },
      },
    });
  }
});
