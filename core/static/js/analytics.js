/**
 * Glint Admin Portal Analytics Charts Engine
 * Powered by Chart.js
 */

document.addEventListener('DOMContentLoaded', () => {
    // Occasions Revenue Chart
    const occasionCtx = document.getElementById('occasionsChart');
    if (occasionCtx && window.occasionLabels && window.occasionValues) {
        new Chart(occasionCtx, {
            type: 'doughnut',
            data: {
                labels: window.occasionLabels,
                datasets: [{
                    data: window.occasionValues,
                    backgroundColor: [
                        '#ff80ab', '#b39ddb', '#80cbc4', '#ffe082',
                        '#a5d6a7', '#90caf9', '#ffb6c1', '#f48fb1'
                    ],
                    borderWidth: 2,
                    borderColor: '#ffffff'
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { position: 'bottom' }
                }
            }
        });
    }

    // Monthly Sales Trend Chart
    const trendCtx = document.getElementById('salesTrendChart');
    if (trendCtx) {
        new Chart(trendCtx, {
            type: 'line',
            data: {
                labels: window.monthlyLabels || [],
                datasets: [{
                    label: 'Paid revenue (₹)',
                    data: window.monthlyValues || [],
                    borderColor: '#d45a6a',
                    backgroundColor: 'rgba(212, 90, 106, 0.12)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 3,
                    pointBackgroundColor: '#d45a6a',
                    pointRadius: 5,
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: (value) => '₹' + value
                        }
                    }
                }
            }
        });
    }
});
