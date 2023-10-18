@Library('libpipelines@add-AT-clouds-features') _

hose {
    EMAIL = 'cd'
    BUILDTOOL = 'make'
    DEVTIMEOUT = 30
    BUILDTOOL_IMAGE = 'golang:1.19'
    VERSIONING_TYPE = 'stratioVersion-3-3'
    UPSTREAM_VERSION = '0.17.0'
    DEPLOYONPRS = true
    GRYPE_TEST = false
    BUILDTOOL_INSTALL = 'make'
    MODULE_LIST = [ "paas.cloud-provisioner:cloud-provisioner:tar.gz"]

    DEV = { config ->
        doPackage(conf: config, parameters: "GOCACHE=/tmp")
        doDeploy(conf: config)
        doDocker(conf: config)
        doAT(conf: config, buildToolOverride: ['BUILDTOOL_IMAGE' : 'qa.int.stratio.com:8443/stratio/kind:%%VERSION'],  configFiles: [[fileId: "Clouds-EKS-yaml", variable: "credentials"]], runOnPR: true)
    }
    INSTALL = { config ->
        doAT(conf: config, buildToolOverride: ['BUILDTOOL_IMAGE' : 'stratio/kind:%%VERSION'], configFiles: ['Clouds-EKS-yaml' : 'credentials'])
    }

    BUILDTOOL_MEMORY_REQUEST = "1024Mi"
    BUILDTOOL_MEMORY_LIMIT = "4096Mi"
}
