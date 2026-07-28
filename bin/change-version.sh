#!/bin/bash -e

BASEDIR=`dirname $0`/..

cd $BASEDIR

if [[ -z "$1" ]]; then
	VERSION=$(cat $BASEDIR/VERSION)
else
	VERSION=$1
fi

VERSION_GO_FILE="$BASEDIR/pkg/cmd/kind/version/version.go"
CORE_VERSION=$(echo "$VERSION" | sed -E "s/-.*//")
# PLT-4515: propagate the real pre-release label (m.3, BUILD, M, or empty for a
# final release) instead of hardcoding SNAPSHOT, so milestone/release builds
# report their true semver without depending on git tags existing at build time.
PRE_RELEASE=$(echo "$VERSION" | sed -E 's/^[0-9]+\.[0-9]+\.[0-9]+(-(.+))?$/\2/')

echo "Modifying cloud-provisioner version to: $1"
echo $VERSION > $BASEDIR/VERSION

sed -i "s/\(const versionCore = \"\)[^\"]*\"/\1$CORE_VERSION\"/" "$VERSION_GO_FILE"
sed -i "s/\(var versionPreRelease = \"\)[^\"]*\"/\1$PRE_RELEASE\"/" "$VERSION_GO_FILE"
