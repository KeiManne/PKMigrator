const path = require('path');
const { BannerPlugin } = require('webpack');
const CopyPlugin = require('copy-webpack-plugin');
const HtmlWebpackPlugin = require('html-webpack-plugin');

const isProduction = process.env.NODE_ENV === 'production';
const sandboxSuffix = '-sandbox';
const widgets = ['index', 'snapshot_widget'];
const entry = {};
for (const widget of widgets) {
  entry[widget] = `./src/widgets/${widget}.tsx`;
  entry[`${widget}${sandboxSuffix}`] = `./src/widgets/${widget}.tsx`;
}

module.exports = {
  mode: isProduction ? 'production' : 'development',
  entry,
  output: {
    path: path.resolve(__dirname, 'dist'),
    filename: '[name].js',
    publicPath: '',
  },
  resolve: { extensions: ['.js', '.jsx', '.ts', '.tsx'] },
  module: {
    rules: [
      {
        test: /\.[tj]sx?$/,
        exclude: /node_modules/,
        loader: 'esbuild-loader',
        options: { loader: 'tsx', target: 'es2021' },
      },
    ],
  },
  plugins: [
    new HtmlWebpackPlugin({
      filename: 'index.html',
      inject: false,
      templateContent: `<!doctype html><html><body><script>
        const params = new URLSearchParams(window.location.search);
        const widgetName = params.get('widgetName');
        if (!widgetName) document.body.textContent = 'Widget ID not specified.';
        else {
          const script = document.createElement('script');
          script.type = 'module';
          script.src = widgetName + '${sandboxSuffix}.js';
          document.body.appendChild(script);
        }
      </script></body></html>`,
    }),
    new BannerPlugin({
      raw: true,
      banner: (file) =>
        file.chunk && !file.chunk.name.includes(sandboxSuffix)
          ? 'const IMPORT_META=import.meta;'
          : '',
    }),
    new CopyPlugin({ patterns: [{ from: 'public', to: '' }, { from: 'README.md', to: '' }] }),
  ],
  devServer: isProduction
    ? undefined
    : {
        port: 8080,
        hot: true,
        compress: true,
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Headers': 'baggage, sentry-trace',
        },
      },
  optimization: { minimize: isProduction },
  watchOptions: isProduction
    ? undefined
    : {
        ignored: /node_modules/,
        poll: 1000,
      },
};
