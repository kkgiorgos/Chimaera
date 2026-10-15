import type { NextConfig } from 'next';

const config: NextConfig = {
  output: 'standalone',
  poweredByHeader: false,
  webpack(config) {
    config.module.rules.push({ test: /\.ttf$/, type: 'asset/resource' });
    return config;
  },
};
export default config;
