#!/bin/bash

# PhoneIDE Android Build Trigger Script
# This script checks the current state and triggers the Android APK build

set -e

echo "🚀 PhoneIDE Android Build Trigger"
echo "================================="

# Configuration
IDE_REPO="ctz168/ide"
PHONEIDE_REPO="ctz168/phoneide"
WORKFLOW_FILE=".github/workflows/build-apk.yml"

# Check if we're in the ide directory
if [ ! -f "server.py" ] || [ ! -f "static/index.html" ]; then
    echo "❌ Error: Not in the ide directory. Please run this script from the ide directory."
    exit 1
fi

echo "📁 Current directory: $(pwd)"
echo "🔍 Checking git status..."

# Check git status
GIT_STATUS=$(git status --porcelain)
if [ ! -z "$GIT_STATUS" ]; then
    echo "⚠️  Uncommitted changes detected:"
    echo "$GIT_STATUS"
    echo "Please commit and push changes first."
    exit 1
fi

echo "✅ Git working directory is clean"

# Check current branch
CURRENT_BRANCH=$(git branch --show-current)
echo "🌿 Current branch: $CURRENT_BRANCH"

if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "⚠️  You're not on the main branch. Switching to main..."
    git checkout main
fi

# Pull latest changes
echo "🔄 Pulling latest changes from origin..."
git pull origin main

# Check if we need to push to remote
LOCAL_COMMIT=$(git rev-parse HEAD)
REMOTE_COMMIT=$(git rev-parse origin/main)

if [ "$LOCAL_COMMIT" != "$REMOTE_COMMIT" ]; then
    echo "📤 Pushing changes to remote..."
    git push origin main
    echo "✅ Code pushed successfully"
    
    # Wait a moment for remote to update
    sleep 3
else
    echo "✅ Code is already up to date"
fi

echo "📋 Current commit: $(git rev-parse --short HEAD)"
echo "📋 Remote commit: $(git rev-parse --short origin/main)"

# Check if this is a fork or the original repo
REMOTE_URL=$(git remote get-url origin)
if [[ "$REMOTE_URL" == *"ctz168/ide"* ]]; then
    echo "✅ Connected to official ctz168/ide repository"
    
    # Now trigger the Android build
    echo ""
    echo "📱 Triggering Android APK build..."
    
    # Check if GitHub CLI is available
    if command -v gh &> /dev/null; then
        echo "🐙 Using GitHub CLI to trigger workflow..."
        
        # Trigger the workflow
        gh workflow run $WORKFLOW_FILE \
            --ref main \
            --field ide_ref=main \
            --field build_type=both
        
        if [ $? -eq 0 ]; then
            echo "✅ Workflow triggered successfully!"
            echo "🔗 You can check the build status at:"
            echo "   https://github.com/$PHONEIDE_REPO/actions"
        else
            echo "❌ Failed to trigger workflow"
            exit 1
        fi
    else
        echo "⚠️  GitHub CLI not available"
        echo "📝 Please manually trigger the build:"
        echo "   1. Go to: https://github.com/$PHONEIDE_REPO/actions"
        echo "   2. Select 'Build & Release APK' workflow"
        echo "   3. Click 'Run workflow' and use these parameters:"
        echo "      - ide_ref: main"
        echo "      - build_type: both"
        echo "   4. Click 'Run workflow' to start the build"
    fi
else
    echo "⚠️  This appears to be a fork. Please push to the official ctz168/ide repository"
    echo "   to trigger the Android build."
    echo ""
    echo "🔗 Official repository: https://github.com/$IDE_REPO"
    echo "📤 Please ensure your remote is set to the official repository"
fi

echo ""
echo "🎯 Build Process Summary:"
echo "   1. ✅ Code committed and pushed to ctz168/ide"
echo "   2. ✅ Android build workflow will clone the latest code"
echo "   3. ✅ APK will be built with multi-select feature included"
echo "   4. ✅ APK will be available in GitHub Releases"
echo ""
echo "⏰ Expected build time: 5-10 minutes"
echo "📦 APK size: ~15-20MB (additional ~300MB for Ubuntu rootfs on first run)"