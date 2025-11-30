document.addEventListener("DOMContentLoaded", () => {
  const catCanvas = document.getElementById("categoryChart");
  if (catCanvas) {
    const cats = JSON.parse(catCanvas.dataset.categoryJson || "[]");
    const labels = cats.map(c => c.category);
    const data = cats.map(c => Math.abs(c.amount));

    new Chart(catCanvas.getContext("2d"), {
      type: "doughnut",
      data: {
        labels,
        datasets: [{
          data,
        }]
      },
      options: {
        plugins: {
          legend: {
            position: "bottom"
          }
        }
      }
    });
  }

  const dailyCanvas = document.getElementById("dailyNetChart");
  if (dailyCanvas) {
    const days = JSON.parse(dailyCanvas.dataset.dailyJson || "[]");
    const labels = days.map(d => d.date);
    const data = days.map(d => d.amount);

    new Chart(dailyCanvas.getContext("2d"), {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "Net",
          data,
          tension: 0.25,
        }]
      },
      options: {
        scales: {
          x: { display: true },
          y: { display: true }
        }
      }
    });
  }
});
