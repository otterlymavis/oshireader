const path = require('path')
const { getDefaultConfig } = require('expo/metro-config')

const projectRoot = __dirname
const config = getDefaultConfig(projectRoot)

config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, 'node_modules'),
]

module.exports = config
