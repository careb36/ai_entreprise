import { describe, it, expect } from 'vitest';
import viteConfig from '../../vite.config';

describe('Vite Optimization & Chunking Strategy Configuration', () => {
  it('should define rollupOptions manualChunks function', () => {
    const buildOptions = viteConfig.build;
    expect(buildOptions).toBeDefined();
    expect(buildOptions?.rollupOptions?.output).toBeDefined();
    
    const output = viteConfig.build?.rollupOptions?.output;
    expect(output).toHaveProperty('manualChunks');
  });

  it('should correctly partition dependencies into chunk vendors', () => {
    const output = viteConfig.build?.rollupOptions?.output as any;
    const manualChunks = output.manualChunks;

    expect(typeof manualChunks).toBe('function');
    
    // Test react vendor grouping
    const reactResult = manualChunks('/project/node_modules/react/index.js');
    expect(reactResult).toBe('vendor-core');

    const reactDomResult = manualChunks('/project/node_modules/react-dom/index.js');
    expect(reactDomResult).toBe('vendor-core');

    // Test icon vendor grouping
    const iconResult = manualChunks('/project/node_modules/lucide-react/dist/index.js');
    expect(iconResult).toBe('vendor-icons');

    // Test general vendor grouping
    const otherResult = manualChunks('/project/node_modules/lodash/lodash.js');
    expect(otherResult).toBe('vendor-utils');

    // App source code should return undefined (default chunking)
    const appResult = manualChunks('/project/src/components/Dashboard.tsx');
    expect(appResult).toBeUndefined();
  });

  it('should enforce asset hashed naming structure for immutable caching', () => {
    const output = viteConfig.build?.rollupOptions?.output as any;
    expect(output.entryFileNames).toContain('[hash]');
    expect(output.chunkFileNames).toContain('[hash]');
    expect(output.assetFileNames).toContain('[hash]');
  });
});
