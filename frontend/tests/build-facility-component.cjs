const fs = require('node:fs');
const path = require('node:path');
const compiled = require('next/dist/compiled/webpack/webpack');
const webpack = compiled.webpack;
const root = path.join(__dirname, '../..');
const output = path.join(root, 'tmp/controls-proof/component');
fs.mkdirSync(output, { recursive: true });
const write = (name, text) => fs.writeFileSync(path.join(output, name), text);
write('loader.cjs', `const ts=require(${JSON.stringify(require.resolve('typescript'))});module.exports=function(source){return ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText;};`);
write('router.js', 'const router={asPath:"/hospital/facility-master",pathname:"/facility-master",isReady:true,query:{},events:{on(){},off(){}},push(){},replace(){},beforePopState(){}};export const useRouter=()=>router;export default router;');
write('link.js', 'import React from "react";export default function Link({href,children,...props}){return React.createElement("a",{...props,href},children)}');
write('entry.js', `import React from 'react';import {createRoot} from 'react-dom/client';import Page from ${JSON.stringify(path.join(root, 'frontend/src/pages/facility-master.tsx'))};import {NavigationBoundary} from ${JSON.stringify(path.join(root, 'frontend/src/components/NavigationBoundary.tsx'))};createRoot(document.getElementById('root')).render(React.createElement(NavigationBoundary,null,React.createElement(Page)));`);
webpack({ mode: 'development', plugins: [new webpack.DefinePlugin({ 'process.env': JSON.stringify({ NODE_ENV: 'development' }) })], entry: path.join(output, 'entry.js'), output: { path: output, filename: 'bundle.js' }, resolve: { extensions: ['.tsx', '.ts', '.js'], modules: [path.join(root, 'frontend/node_modules'), 'node_modules'], alias: { 'next/router': path.join(output, 'router.js'), 'next/link': path.join(output, 'link.js') } }, module: { rules: [{ test: /\.tsx?$/, exclude: /node_modules/, use: path.join(output, 'loader.cjs') }] } }, (error, stats) => {
  if (error || stats.hasErrors()) { console.error(error || stats.toString({ all: false, errors: true })); process.exitCode = 1; }
  else console.log('Actual facility page component bundle built; only Next navigation adapters replaced.');
});
