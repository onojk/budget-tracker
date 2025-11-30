document.addEventListener("DOMContentLoaded", () => {
  const catCanvas = document.getElementById("categoryChart");
  if (catCanvas && categoryLabels.length > 0) {
    new Chart(catCanvas, {
      type: "bar",
      data: {
        labels: categoryLabels,
        datasets: [{
          label: "Total by Category",
          data: categoryTotals.map(v => Math.abs(v)), // show magnitude
        }]
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false }
        },
        scales: {
          y: {
            beginAtZero: true
          }
        }
      }
    });
  }

  const dailyCanvas = document.getElementById("dailyChart");
  if (dailyCanvas && dailyLabels.length > 0) {
    new Chart(dailyCanvas, {
      type: "line",
      data: {
        labels: dailyLabels,
        datasets: [{
          label: "Net per Day",
          data: dailyValues,
          tension: 0.3
        }]
      },
      options: {
        responsive: true,
        scales: {
          y: { beginAtZero: false }
        }
      }
    });
  }
});
