module.exports = {
  ci: {
    collect: {
      staticDistDir: './frontend/dist',
      numberOfRuns: 3,
    },
    assert: {
      assertions: {
        'categories:performance': ['error', { minScore: 0.90 }], // Performance threshold > 90 (Doherty Threshold support)
        'first-contentful-paint': ['error', { maxNumericValue: 1000 }], // FCP < 1.0s
        'largest-contentful-paint': ['error', { maxNumericValue: 1500 }], // LCP < 1.5s
        'total-blocking-time': ['error', { maxNumericValue: 100 }], // TBT < 100ms
        'speed-index': ['error', { maxNumericValue: 1200 }],
      },
    },
    upload: {
      target: 'temporary-public-storage',
    },
  },
};
